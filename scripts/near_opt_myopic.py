import itertools
import logging
import math
import os

import numpy as np
import pandas as pd
import pypsa
from _benchmark import memory_logger
from _helpers import (
    configure_logging,
    set_scenario_config,
    update_config_from_wildcards,
)
from linopy import LinearExpression, QuadraticExpression, merge
from prepare_sector_network import set_temporal_aggregation
from pypsa.descriptors import nominal_attrs
from solve_network import (
    aggregate_build_years,
    disaggregate_build_years,
    extra_functionality,
    prepare_network,
)

logger = logging.getLogger(__name__)
pypsa.pf.logger.setLevel(logging.WARNING)


def optimize_mga_fixed_bound(
    n,
    obj_bound,
    weights,
    sense="min",
    obj_bound_scaling_factor=1.0,
    model_kwargs={},
    **kwargs,
):
    """
    Run modelling-to-generate-alternatives (MGA) on network to find
    near-optimal solutions. Modification of
    pypsa.optimize.abstract.optimize_mga with direct cost bound
    argument.

    Parameters
    ----------
    n : pypsa.Network
    obj_bound : float
        Right hand side on total system cost constraint.
    weights : dict-like
        Weights for alternate objective function. The default is None, which
        minimizes generation capacity. The weights dictionary should be keyed
        with the component and variable (see ``pypsa/variables.csv``), followed
        by a float, dict, pd.Series or pd.DataFrame for the coefficients of the
        objective function. Examples:

        >>> {"Generator": {"p_nom": 1}}
        >>> {"Generator": {"p_nom": pd.Series(1, index=n.generators.index)}}
        >>> {"Generator": {"p_nom": {"gas": 1, "coal": 2}}}
        >>> {"Generator": {"p": pd.Series(1, index=n.generators.index)}
        >>> {"Generator": {"p": pd.DataFrame(1, columns=n.generators.index, index=n.snapshots)}

        Weights for non-extendable components are ignored. The dictionary does
        not need to provide weights for all extendable components.
    sense : str|int
        Optimization sense of alternate objective function. Defaults to 'min'.
        Can also be 'max'.
    model_kwargs: dict
        Keyword arguments used by `linopy.Model`, such as `solver_dir` or
        `chunk`.
    **kwargs:
        Keyword argument used by `linopy.Model.solve`, such as `solver_name`,

    Returns
    status : str
        The status of the optimization, either "ok" or one of the codes listed
        in https://linopy.readthedocs.io/en/latest/generated/linopy.constants.SolverStatus.html
    condition : str
        The termination condition of the optimization, either
        "optimal" or one of the codes listed in
        https://linopy.readthedocs.io/en/latest/generated/linopy.constants.TerminationCondition.html
    """
    if weights is None:
        weights = dict(Generator=dict(p_nom=pd.Series(1, index=n.generators.index)))

    # create basic model
    m = n.optimize.create_model(
        snapshots=n.snapshots,
        **model_kwargs,
    )

    # build budget constraint
    fixed_cost = n.statistics.installed_capex().sum()
    objective = m.objective
    if not isinstance(objective, (LinearExpression, QuadraticExpression)):
        objective = objective.expression

    name = "total_system_cost"
    # Add globalconstraint object so dual variable can be registered (if it doesn't already exist)
    if not name in n.global_constraints.index:
        n.add(
            "GlobalConstraint",
            name=name,
            type="budget",
            carrier_attribute="",
            sense="<=",
            constant=obj_bound * obj_bound_scaling_factor,
        )

    # Add constraint to model
    m.add_constraints(
        (objective + fixed_cost) * obj_bound_scaling_factor
        <= obj_bound * obj_bound_scaling_factor,
        name=f"GlobalConstraint-{name}",
    )

    # parse optimization sense
    if (
        isinstance(sense, str)
        and sense.startswith("min")
        or isinstance(sense, int)
        and sense > 0
    ):
        sense = 1
    elif (
        isinstance(sense, str)
        and sense.startswith("max")
        or isinstance(sense, int)
        and sense < 0
    ):
        sense = -1
    else:
        raise ValueError(f"Could not parse optimization sense {sense}")

    # build alternate objective
    objective = []
    for c, attrs in weights.items():
        for attr, coeffs in attrs.items():
            if isinstance(coeffs, dict):
                coeffs = pd.Series(coeffs)
            if attr == nominal_attrs[c] and isinstance(coeffs, pd.Series):
                coeffs = coeffs.reindex(n.get_extendable_i(c))
                coeffs.index.name = ""
            elif isinstance(coeffs, pd.Series):
                coeffs = coeffs.reindex(columns=n.df(c).index)
            elif isinstance(coeffs, pd.DataFrame):
                coeffs = coeffs.reindex(columns=n.df(c).index, index=n.snapshots)
            objective.append(m[f"{c}-{attr}"] * coeffs * sense)

    m.objective = merge(objective)

    status, condition = n.optimize.solve_model(**kwargs)

    # write MGA coefficients into metadata
    n.meta["obj_bound"] = obj_bound
    n.meta["sense"] = sense

    def convert_to_dict(obj):
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient="list")
        elif isinstance(obj, pd.Series):
            return obj.to_dict()
        elif isinstance(obj, dict):
            return {k: convert_to_dict(v) for k, v in obj.items()}
        else:
            return obj

    n.meta["weights"] = convert_to_dict(weights)

    return status, condition


def prepare_solver_options(solving):
    # The following solver setup follows that of `solve_network` in `solve_network.py`:
    set_of_options = solving["solver"]["options"]
    cf_solving = solving["options"]

    kwargs = {}

    kwargs["solver_options"] = (
        solving["solver_options"][set_of_options] if set_of_options else {}
    )
    kwargs["solver_name"] = solving["solver"]["name"]
    kwargs["extra_functionality"] = extra_functionality
    kwargs["assign_all_duals"] = cf_solving.get("assign_all_duals", False)
    kwargs["io_api"] = cf_solving.get("io_api", None)

    model_kwargs = {}
    model_kwargs["transmission_losses"] = cf_solving.get("transmission_losses", False)
    model_kwargs["linearized_unit_commitment"] = cf_solving.get(
        "linearized_unit_commitment", False
    )

    if kwargs["solver_name"] == "gurobi":
        logging.getLogger("gurobipy").setLevel(logging.CRITICAL)

    if "model_options" in solving:
        model_kwargs = model_kwargs | solving["model_options"]

        if "solver_dir" in model_kwargs and "$" in model_kwargs["solver_dir"]:
            # Resolve env var as path
            model_kwargs["solver_dir"] = os.path.expandvars(model_kwargs["solver_dir"])
            logger.info(f"Set solver_dir to {model_kwargs['solver_dir']}")

    return kwargs, model_kwargs


def near_opt(
    n,
    config,
    params,
    solving,
    near_opt_config,
    build_year_agg,
    current_horizon,
    sense,
    cost_bound,
):
    kwargs, model_kwargs = prepare_solver_options(solving)

    n.config = config
    n.params = params

    build_year_agg_enabled = build_year_agg["enable"] and (
        config["foresight"] == "myopic"
    )
    if build_year_agg_enabled:
        indices = aggregate_build_years(
            n, exclude_carriers=build_year_agg["exclude_carriers"]
        )

    weights = {}
    static = near_opt_config["weights"].get("static", {})
    for c in static:
        vars = {}
        for v in static[c]:
            w = pd.Series(0, index=n.df(c).index)
            for carrier, const in static[c][v].items():
                w.loc[(n.df(c).carrier == carrier) & n.df(c).p_nom_extendable] = const
            vars[v] = w
        weights[c] = vars
    varying = near_opt_config["weights"].get("varying", {})
    for c in varying:
        vars = {}
        for v in varying[c]:
            w = pd.DataFrame(0, columns=n.df(c).index, index=n.snapshots)
            for carrier, const in varying[c][v].items():
                w.loc[:, n.df(c).carrier == carrier] = const
            w = w.multiply(n.snapshot_weightings.objective, axis=0)
            vars[v] = w
        weights[c] = vars

    status, condition = optimize_mga_fixed_bound(
        n,
        cost_bound,
        weights=weights,
        sense=sense,
        obj_bound_scaling_factor=float(
            near_opt_config.get("obj_bound_scaling_factor", 1.0)
        ),
        model_kwargs=model_kwargs,
        **kwargs,
    )

    if status == "ok":
        print("Solved successfully")
        n.meta["near_opt_status"] = "success"

        if build_year_agg_enabled:
            del n.model
            disaggregate_build_years(n, indices, current_horizon)

        return n
    elif (
        (status == "warning")
        and (condition == "other")
        and ("numeric" not in solving["solver"]["options"])
    ):
        # It's possible that the model just needs to be solved with increased numerical focus
        logger.warning(
            "Solving possibly failed due to numerical trouble. "
            "Trying again with increased numerical focus."
        )
        solving["solver"]["options"] = solving["solver"]["options"] + "-numeric"
        n = near_opt(
            n,
            config,
            params,
            solving,
            near_opt_config,
            {"enable": False},  # Note: don't "aggregate twice"
            current_horizon,
            sense,
            cost_bound,
        )

        if build_year_agg_enabled:
            del n.model
            disaggregate_build_years(n, indices, current_horizon)

        return n

    elif "infeasible" in condition:
        # First, try to solve to optimality instead, in case the model
        # was infeasible because of the objective bound.
        logger.warning("Cost bound too tight! Solved to cost-optimality instead.")
        del model_kwargs["transmission_losses"]
        del model_kwargs["linearized_unit_commitment"]
        kwargs["model_kwargs"] = model_kwargs
        status, condition = n.optimize(**kwargs)

        if status == "ok":
            logger.info("Cost-optimisation successful")
            n.meta["near_opt_status"] = "too_expensive"
            n.meta["opt_system_cost"] = (
                n.statistics.installed_capex().sum() + n.objective
            )
            if build_year_agg_enabled:
                disaggregate_build_years(n, indices, current_horizon)
            return n

        elif "infeasible" in condition:
            if cf_solving.get("print_infeasibilities", True):
                labels = n.model.compute_infeasibilities()
                logger.info(f"Labels:\n{labels}")
                n.model.print_infeasibilities()

    raise RuntimeError(
        f"Solve with condition status {status} and condition {condition}"
    )


def disable_near_opt_components(n, near_opt_config):
    for _, components in near_opt_config["weights"].items():
        for component, variables in components.items():
            for var, carriers in variables.items():
                for carrier in carriers:
                    # Set '{var}_nom_extendable' to False for carrier
                    n.df(component).loc[
                        n.df(component).carrier == carrier, f"{var}_nom_extendable"
                    ] = False


def near_opt_try_zero_low_res(
    n,
    config,
    params,
    build_year_agg,
    planning_horizon,
    near_opt_config,
    kwargs,
    cost_bound,
):
    # First, check on a highly aggregated version of the network if a
    # zero objective can be achieved; use 10 time segments.
    N = len(n.snapshot_weightings)
    NEW_RES = 20
    if N <= NEW_RES:
        # If the model is already very low resolution, just answer yes
        # to the heuristic question.
        return True
    else:
        # Cut the snapshots into a few segments
        interval = math.ceil(N / NEW_RES)

        # Create bins for the index
        bins = pd.cut(
            range(N),
            bins=np.arange(0, N + interval, interval),
            right=False,
            labels=False,
        )

        # Group by bins and sum the values
        new_sn = n.snapshot_weightings.groupby(bins).sum()
        new_sn.index = n.snapshot_weightings.index[::interval]

        # Create a new network with the aggregated snapshots
        m = set_temporal_aggregation(n, f"{NEW_RES}seg", new_sn)

    m.config = config
    m.params = params

    # Now, remove all components listed in the near-opt config
    disable_near_opt_components(m, near_opt_config)

    build_year_agg_enabled = build_year_agg["enable"] and (
        config["foresight"] == "myopic"
    )
    if build_year_agg_enabled:
        aggregate_build_years(m, exclude_carriers=build_year_agg["exclude_carriers"])

    # Solve to optimality
    status, condition = m.optimize(**kwargs)

    # Check that the total system cost is within the bounds
    total_system_cost = m.statistics.capex().sum() + m.statistics.opex().sum()
    if (status == "ok") and (total_system_cost <= cost_bound):
        logger.info("Zero objective achieved with low resolution network")
        return True

    return False


def near_opt_try_zero(
    n,
    config,
    params,
    solving,
    build_year_agg,
    planning_horizon,
    near_opt_config,
    cost_bound,
):
    """
    Try a fast path for near-optimal computation if the objective is zero.
    """
    kwargs, model_kwargs = prepare_solver_options(solving)
    del model_kwargs["transmission_losses"]
    del model_kwargs["linearized_unit_commitment"]
    kwargs["model_kwargs"] = model_kwargs

    # First, check on a highly aggregated version of the network if a
    # zero objective can be achieved; use 10 time segments.
    if near_opt_try_zero_low_res(
        n,
        config,
        params,
        build_year_agg,
        planning_horizon,
        near_opt_config,
        kwargs,
        cost_bound,
    ):
        # At this point, we can try to solve the network to optimality
        # without the near-opt components just like at low resolution;
        # this time without temporal aggregation.
        m = n.copy()

        m.config = config
        m.params = params

        # Now, remove all components listed in the near-opt config
        disable_near_opt_components(m, near_opt_config)

        # Aggregation by build year
        build_year_agg_enabled = build_year_agg["enable"] and (
            config["foresight"] == "myopic"
        )
        if build_year_agg_enabled:
            indices = aggregate_build_years(
                m, exclude_carriers=build_year_agg["exclude_carriers"]
            )

        # Solve to optimality
        status, condition = m.optimize(**kwargs)

        # Check that the total system cost is within the bounds
        total_system_cost = m.statistics.capex().sum() + m.statistics.opex().sum()
        if (status == "ok") and (total_system_cost <= cost_bound):
            # At this point, we have a network that is near-optimal
            # and good enough for our purposes. Don't forget to
            # disaggregate.
            logger.info(
                "Zero objective achieved by turning off components and solving to optimality"
            )

            if build_year_agg_enabled:
                del m.model
                disaggregate_build_years(m, indices, planning_horizon)

            return m, True
    logger.info(
        "It looks like the objective for near-opt minimisation is strictly positive."
    )
    return None, False


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake(
            "solve_sector_network",
            configfiles="../config/test/config.perfect.yaml",
            simpl="",
            opts="",
            clusters="37",
            ll="v1.0",
            sector_opts="CO2L0-1H-T-H-B-I-A-dist1",
            planning_horizons="2030",
        )

    # Follow `solve_network.py` for how to set up logging,
    # configuration, etc.
    configure_logging(snakemake)
    set_scenario_config(snakemake)
    update_config_from_wildcards(snakemake.config, snakemake.wildcards)

    solve_opts = snakemake.params.solving["options"]

    np.random.seed(solve_opts.get("seed", 123))

    n = pypsa.Network(snakemake.input.network)
    planning_horizons = snakemake.params.planning_horizons
    current_horizon = snakemake.wildcards.planning_horizons

    n = prepare_network(
        n,
        solve_opts,
        clusters=snakemake.wildcards.clusters,
        config=snakemake.config,
        sector=snakemake.params.sector,
        foresight="myopic",
        planning_horizons=planning_horizons,
        current_horizon=current_horizon,
    )

    # Calculate slack. We gradually increase slack from half the
    # nominal slack at the first planning horizon to the full slack at
    # the last planning horizon.
    slack_nom = float(snakemake.wildcards.slack)
    slack_initial_fraction = snakemake.params.near_opt.get(
        "slack_initial_fraction", 1.0
    )
    planning_horizon_frac = (int(current_horizon) - min(planning_horizons)) / (
        max(planning_horizons) - min(planning_horizons)
    )
    slack = slack_nom * (
        slack_initial_fraction + (1 - slack_initial_fraction) * planning_horizon_frac
    )
    logger.info(f"Slack for horizon {current_horizon}: {slack}")

    # Base objective slack on optimal network.
    n_opt = pypsa.Network(snakemake.input.network_opt)
    obj_base = n_opt.statistics.capex().sum() + n_opt.statistics.opex().sum()
    slack_absolute = slack * obj_base
    if snakemake.params.build_year_agg["enable"]:
        aggregate_build_years(
            n_opt, exclude_carriers=snakemake.params.build_year_agg["exclude_carriers"]
        )
        obj_base = n_opt.statistics.capex().sum() + n_opt.statistics.opex().sum()
    del n_opt

    with memory_logger(
        filename=getattr(snakemake.log, "memory", None), interval=30.0
    ) as mem:
        # Often, we can get a zero objective, and try a fast path for this.
        fast_path_success = False
        if snakemake.wildcards.sense == "min":
            m, fast_path_success = near_opt_try_zero(
                n,
                snakemake.config,
                snakemake.params,
                snakemake.params.solving,
                snakemake.params.build_year_agg,
                current_horizon,
                snakemake.params.near_opt,
                obj_base + slack_absolute,
            )

        if not fast_path_success:
            m = near_opt(
                n,
                snakemake.config,
                snakemake.params,
                snakemake.params.solving,
                snakemake.params.near_opt,
                snakemake.params.build_year_agg,
                current_horizon,
                snakemake.wildcards.sense,
                obj_base + slack_absolute,
            )

    logger.info(f"Maximum memory usage: {mem.mem_usage}")

    m.export_to_netcdf(snakemake.output[0])
