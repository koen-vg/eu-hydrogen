# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2017-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Solves optimal operation and capacity for a network with the option to
iteratively optimize while updating line reactances.

This script is used for optimizing the electrical network as well as the
sector coupled network.

Description
-----------

Total annual system costs are minimised with PyPSA. The full formulation of the
linear optimal power flow (plus investment planning
is provided in the
`documentation of PyPSA <https://pypsa.readthedocs.io/en/latest/optimal_power_flow.html#linear-optimal-power-flow>`_.

The optimization is based on the :func:`network.optimize` function.
Additionally, some extra constraints specified in :mod:`solve_network` are added.

.. note::

    The rules ``solve_elec_networks`` and ``solve_sector_networks`` run
    the workflow for all scenarios in the configuration file (``scenario:``)
    based on the rule :mod:`solve_network`.
"""
import importlib
import logging
import os
import re
import sys
import time

import numpy as np
import pandas as pd
import pypsa
import xarray as xr
import yaml
from _benchmark import memory_logger
from _helpers import (
    configure_logging,
    set_scenario_config,
    update_config_from_wildcards,
)
from prepare_sector_network import get
from pypsa.clustering.spatial import align_strategies, flatten_multiindex
from pypsa.descriptors import Dict, get_activity_mask
from pypsa.descriptors import get_switchable_as_dense as get_as_dense
from pypsa.descriptors import nominal_attrs
from pypsa.io import import_components_from_dataframe, import_series_from_dataframe

logger = logging.getLogger(__name__)
pypsa.pf.logger.setLevel(logging.WARNING)


def add_land_use_constraint_perfect(n):
    """
    Add global constraints for tech capacity limit.
    """
    logger.info("Add land-use constraint for perfect foresight")

    def compress_series(s):
        def process_group(group):
            if group.nunique() == 1:
                return pd.Series(group.iloc[0], index=[None])
            else:
                return group

        return s.groupby(level=[0, 1]).apply(process_group)

    def new_index_name(t):
        # Convert all elements to string and filter out None values
        parts = [str(x) for x in t if x is not None]
        # Join with space, but use a dash for the last item if not None
        return " ".join(parts[:2]) + (f"-{parts[-1]}" if len(parts) > 2 else "")

    def check_p_min_p_max(p_nom_max):
        p_nom_min = n.generators[ext_i].groupby(grouper).sum().p_nom_min
        p_nom_min = p_nom_min.reindex(p_nom_max.index)
        check = (
            p_nom_min.groupby(level=[0, 1]).sum()
            > p_nom_max.groupby(level=[0, 1]).min()
        )
        if check.sum():
            logger.warning(
                f"summed p_min_pu values at node larger than technical potential {check[check].index}"
            )

    grouper = [n.generators.carrier, n.generators.bus, n.generators.build_year]
    ext_i = n.generators.p_nom_extendable
    # get technical limit per node and investment period
    p_nom_max = n.generators[ext_i].groupby(grouper).min().p_nom_max
    # drop carriers without tech limit
    p_nom_max = p_nom_max[~p_nom_max.isin([np.inf, np.nan])]
    # carrier
    carriers = p_nom_max.index.get_level_values(0).unique()
    gen_i = n.generators[(n.generators.carrier.isin(carriers)) & (ext_i)].index
    n.generators.loc[gen_i, "p_nom_min"] = 0
    # check minimum capacities
    check_p_min_p_max(p_nom_max)
    # drop multi entries in case p_nom_max stays constant in different periods
    # p_nom_max = compress_series(p_nom_max)
    # adjust name to fit syntax of nominal constraint per bus
    df = p_nom_max.reset_index()
    df["name"] = df.apply(
        lambda row: f"nom_max_{row['carrier']}"
        + (f"_{row['build_year']}" if row["build_year"] is not None else ""),
        axis=1,
    )

    for name in df.name.unique():
        df_carrier = df[df.name == name]
        bus = df_carrier.bus
        n.buses.loc[bus, name] = df_carrier.p_nom_max.values

    return n


def add_land_use_constraint(n, current_horizon):
    # warning: this will miss existing offwind which is not classed AC-DC and has carrier 'offwind'

    for carrier in [
        "solar",
        "solar rooftop",
        "solar-hsat",
        "onwind",
        "offwind-ac",
        "offwind-dc",
        "offwind-float",
    ]:

        ext_i = (n.generators.carrier == carrier) & ~n.generators.p_nom_extendable
        existing = (
            n.generators.loc[ext_i, "p_nom"]
            .groupby(n.generators.bus.map(n.buses.location))
            .sum()
        )
        existing.index += " " + carrier + "-" + current_horizon
        n.generators.loc[existing.index, "p_nom_max"] -= existing

    # check if existing capacities are larger than technical potential
    existing_large = n.generators[
        n.generators["p_nom_min"] > n.generators["p_nom_max"]
    ].index
    if len(existing_large):
        logger.warning(
            f"Existing capacities larger than technical potential for {existing_large},\
                        adjust technical potential to existing capacities"
        )
        n.generators.loc[existing_large, "p_nom_max"] = n.generators.loc[
            existing_large, "p_nom_min"
        ]

    n.generators["p_nom_max"] = n.generators["p_nom_max"].clip(lower=0)


def add_solar_potential_constraints(n, config):
    """
    Add constraint to make sure the sum capacity of all solar technologies (fixed, tracking, ets. ) is below the region potential.
    Example:
    ES1 0: total solar potential is 10 GW, meaning:
           solar potential : 10 GW
           solar-hsat potential : 8 GW (solar with single axis tracking is assumed to have higher land use)
    The constraint ensures that:
           solar_p_nom + solar_hsat_p_nom * 1.13 <= 10 GW
    """
    land_use_factors = {
        "solar-hsat": config["renewable"]["solar"]["capacity_per_sqkm"]
        / config["renewable"]["solar-hsat"]["capacity_per_sqkm"]
    }

    solar_carriers = ["solar", "solar-hsat"]
    solar = n.generators.loc[n.generators.carrier == "solar"]
    all_solar = n.generators.loc[n.generators.carrier.isin(solar_carriers)]

    # Separate extendable and non-extendable generators
    solar_ext = solar.loc[solar.p_nom_extendable]
    solar_non_ext = solar.loc[~solar.p_nom_extendable]
    all_solar_ext = all_solar.loc[all_solar.p_nom_extendable]
    all_solar_non_ext = all_solar.loc[~all_solar.p_nom_extendable]

    if all_solar_ext.empty:
        return

    land_use = pd.Series(1, index=all_solar.index, name="land_use_factor")
    for carrier, factor in land_use_factors.items():
        land_use.loc[all_solar.carrier == carrier] *= factor

    location = n.buses.index.to_series()
    ggrouper = all_solar.bus

    rhs = pd.concat([solar_non_ext.p_nom, solar_ext.p_nom_max]).groupby(ggrouper).sum()

    rename = {"Generator-ext": "Generator"}
    lhs = (
        n.model["Generator-p_nom"].rename(rename).loc[all_solar_ext.index]
        * land_use.loc[all_solar_ext.index]
    ).groupby(ggrouper.loc[all_solar_ext.index]).sum() + (
        all_solar_non_ext.p_nom * land_use.loc[all_solar_non_ext.index]
    ).groupby(
        ggrouper.loc[all_solar_non_ext.index]
    ).sum()

    logger.info("Adding solar potential constraint.")
    n.model.add_constraints(lhs <= rhs, name="solar_potential")


def add_co2_sequestration_limit(n, limit_dict, current_horizon):
    """
    Add a global constraint on the amount of Mt CO2 that can be sequestered.
    """

    if not n.investment_periods.empty:
        periods = n.investment_periods
        limit = pd.Series(
            {
                f"co2_sequestration_limit-{period}": limit_dict.get(period, 200)
                for period in periods
            }
        )
        names = limit.index
    else:
        limit = get(limit_dict, int(current_horizon))
        periods = [np.nan]
        names = pd.Index(["co2_sequestration_limit"])

    n.madd(
        "GlobalConstraint",
        names,
        sense=">=",
        constant=-limit * 1e6,
        type="operational_limit",
        carrier_attribute="co2 sequestered",
        investment_period=periods,
    )


def add_carbon_constraint(n, snapshots):
    glcs = n.global_constraints.query('type == "co2_atmosphere"')
    if glcs.empty:
        return
    for name, glc in glcs.iterrows():
        carattr = glc.carrier_attribute
        emissions = n.carriers.query(f"{carattr} != 0")[carattr]

        if emissions.empty:
            continue

        # stores
        bus_carrier = n.stores.bus.map(n.buses.carrier)
        stores = n.stores[bus_carrier.isin(emissions.index) & ~n.stores.e_cyclic]
        if not stores.empty:
            last = n.snapshot_weightings.reset_index().groupby("period").last()
            last_i = last.set_index([last.index, last.timestep]).index
            final_e = n.model["Store-e"].loc[last_i, stores.index]
            time_valid = int(glc.loc["investment_period"])
            time_i = pd.IndexSlice[time_valid, :]
            lhs = final_e.loc[time_i, :] - final_e.shift(snapshot=1).loc[time_i, :]

            rhs = glc.constant
            n.model.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{name}")


def add_carbon_budget_constraint(n, snapshots):
    glcs = n.global_constraints.query('type == "Co2Budget"')
    if glcs.empty:
        return
    for name, glc in glcs.iterrows():
        carattr = glc.carrier_attribute
        emissions = n.carriers.query(f"{carattr} != 0")[carattr]

        if emissions.empty:
            continue

        # stores
        bus_carrier = n.stores.bus.map(n.buses.carrier)
        stores = n.stores[bus_carrier.isin(emissions.index) & ~n.stores.e_cyclic]
        if not stores.empty:
            last = n.snapshot_weightings.reset_index().groupby("period").last()
            last_i = last.set_index([last.index, last.timestep]).index
            final_e = n.model["Store-e"].loc[last_i, stores.index]
            time_valid = int(glc.loc["investment_period"])
            time_i = pd.IndexSlice[time_valid, :]
            weighting = n.investment_period_weightings.loc[time_valid, "years"]
            lhs = final_e.loc[time_i, :] * weighting

            rhs = glc.constant
            n.model.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{name}")


def add_max_growth(n, opts):
    """
    Add maximum growth rates for different carriers.
    """

    # take maximum yearly difference between investment periods since historic growth is per year
    factor = n.investment_period_weightings.years.max() * opts["factor"]
    for carrier in opts["max_growth"].keys():
        max_per_period = opts["max_growth"][carrier] * factor
        logger.info(
            f"set maximum growth rate per investment period of {carrier} to {max_per_period} GW."
        )
        n.carriers.loc[carrier, "max_growth"] = max_per_period * 1e3

    for carrier in opts["max_relative_growth"].keys():
        max_r_per_period = opts["max_relative_growth"][carrier]
        logger.info(
            f"set maximum relative growth per investment period of {carrier} to {max_r_per_period}."
        )
        n.carriers.loc[carrier, "max_relative_growth"] = max_r_per_period

    return n


def add_retrofit_gas_boiler_constraint(n, snapshots):
    """
    Allow retrofitting of existing gas boilers to H2 boilers.
    """
    c = "Link"
    logger.info("Add constraint for retrofitting gas boilers to H2 boilers.")
    # existing gas boilers
    mask = n.links.carrier.str.contains("gas boiler") & ~n.links.p_nom_extendable
    gas_i = n.links[mask].index
    mask = n.links.carrier.str.contains("retrofitted H2 boiler")
    h2_i = n.links[mask].index

    n.links.loc[gas_i, "p_nom_extendable"] = True
    p_nom = n.links.loc[gas_i, "p_nom"]
    n.links.loc[gas_i, "p_nom"] = 0

    # heat profile
    cols = n.loads_t.p_set.columns[
        n.loads_t.p_set.columns.str.contains("heat")
        & ~n.loads_t.p_set.columns.str.contains("industry")
        & ~n.loads_t.p_set.columns.str.contains("agriculture")
    ]
    profile = n.loads_t.p_set[cols].div(
        n.loads_t.p_set[cols].groupby(level=0).max(), level=0
    )
    # to deal if max value is zero
    profile.fillna(0, inplace=True)
    profile.rename(columns=n.loads.bus.to_dict(), inplace=True)
    profile = profile.reindex(columns=n.links.loc[gas_i, "bus1"])
    profile.columns = gas_i

    rhs = profile.mul(p_nom)

    dispatch = n.model["Link-p"]
    active = get_activity_mask(n, c, snapshots, gas_i)
    rhs = rhs[active]
    p_gas = dispatch.sel(Link=gas_i)
    p_h2 = dispatch.sel(Link=h2_i)

    lhs = p_gas + p_h2

    n.model.add_constraints(lhs == rhs, name="gas_retrofit")


def prepare_network(
    n,
    solve_opts=None,
    clusters=None,
    config=None,
    sector=None,
    foresight=None,
    planning_horizons=None,
    current_horizon=None,
):
    if "clip_p_max_pu" in solve_opts:
        for df in (
            n.generators_t.p_max_pu,
            n.generators_t.p_min_pu,
            n.links_t.p_max_pu,
            n.links_t.p_min_pu,
            n.storage_units_t.inflow,
        ):
            df.where(df > solve_opts["clip_p_max_pu"], other=0.0, inplace=True)

    if load_shedding := solve_opts.get("load_shedding"):
        # intersect between macroeconomic and surveybased willingness to pay
        # http://journal.frontiersin.org/article/10.3389/fenrg.2015.00055/full
        # TODO: retrieve color and nice name from config
        n.add("Carrier", "load", color="#dd2e23", nice_name="Load shedding")
        buses_i = n.buses.index
        if not np.isscalar(load_shedding):
            # TODO: do not scale via sign attribute (use Eur/MWh instead of Eur/kWh)
            load_shedding = 1e2  # Eur/kWh

        n.madd(
            "Generator",
            buses_i,
            " load",
            bus=buses_i,
            carrier="load",
            sign=1e-3,  # Adjust sign to measure p and p_nom in kW instead of MW
            marginal_cost=load_shedding,  # Eur/kWh
            p_nom=1e9,  # kW
        )

    if solve_opts.get("curtailment_mode"):
        n.add("Carrier", "curtailment", color="#fedfed", nice_name="Curtailment")
        n.generators_t.p_min_pu = n.generators_t.p_max_pu
        buses_i = n.buses.query("carrier == 'AC'").index
        n.madd(
            "Generator",
            buses_i,
            suffix=" curtailment",
            bus=buses_i,
            p_min_pu=-1,
            p_max_pu=0,
            marginal_cost=-0.1,
            carrier="curtailment",
            p_nom=1e6,
        )

    if solve_opts.get("noisy_costs"):
        for t in n.iterate_components():
            # if 'capital_cost' in t.df:
            #    t.df['capital_cost'] += 1e1 + 2.*(np.random.random(len(t.df)) - 0.5)
            if "marginal_cost" in t.df:
                t.df["marginal_cost"] += 1e-2 + 2e-3 * (
                    np.random.random(len(t.df)) - 0.5
                )

        for t in n.iterate_components(["Line", "Link"]):
            t.df["capital_cost"] += (
                1e-1 + 2e-2 * (np.random.random(len(t.df)) - 0.5)
            ) * t.df["length"]

    if solve_opts.get("nhours"):
        nhours = solve_opts["nhours"]
        n.set_snapshots(n.snapshots[:nhours])
        n.snapshot_weightings[:] = 8760.0 / nhours

    if foresight == "myopic":
        add_land_use_constraint(n, current_horizon)

    if foresight == "perfect":
        n = add_land_use_constraint_perfect(n)
        if sector and sector["limit_max_growth"]["enable"]:
            n = add_max_growth(n, sector["limit_max_growth"])

    if n.stores.carrier.eq("co2 sequestered").any():
        limit_dict = sector["co2_sequestration_potential"]
        add_co2_sequestration_limit(
            n, limit_dict=limit_dict, current_horizon=current_horizon
        )

    return n


def add_CCL_constraints(n, config, current_horizon):
    """
    Add CCL (country & carrier limit) constraint to the network.

    Add minimum and maximum levels of generator nominal capacity per carrier
    for individual countries. Opts and path for agg_p_nom_minmax.csv must be defined
    in config.yaml. Default file is available at data/agg_p_nom_minmax.csv.

    Parameters
    ----------
    n : pypsa.Network
    config : dict

    Example
    -------
    scenario:
        opts: [Co2L-CCL-24h]
    electricity:
        agg_p_nom_limits: data/agg_p_nom_minmax.csv
    """
    agg_p_nom_minmax = pd.read_csv(
        config["solving"]["agg_p_nom_limits"]["file"], index_col=[0, 1], header=[0, 1]
    )[current_horizon]
    logger.info("Adding generation capacity constraints per carrier and country")
    p_nom = n.model["Generator-p_nom"]

    gens = n.generators.query("p_nom_extendable").rename_axis(index="Generator-ext")
    if config["solving"]["agg_p_nom_limits"]["agg_offwind"]:
        rename_offwind = {
            "offwind-ac": "offwind-all",
            "offwind-dc": "offwind-all",
            "offwind": "offwind-all",
        }
        gens = gens.replace(rename_offwind)
    grouper = pd.concat([gens.bus.map(n.buses.country), gens.carrier], axis=1)
    lhs = p_nom.groupby(grouper).sum().rename(bus="country")

    if config["solving"]["agg_p_nom_limits"]["include_existing"]:
        gens_cst = n.generators.query("~p_nom_extendable").rename_axis(
            index="Generator-cst"
        )
        gens_cst = gens_cst[
            (gens_cst["build_year"] + gens_cst["lifetime"])
            >= int(current_horizon)
        ]
        if config["solving"]["agg_p_nom_limits"]["agg_offwind"]:
            gens_cst = gens_cst.replace(rename_offwind)
        rhs_cst = (
            pd.concat(
                [gens_cst.bus.map(n.buses.country), gens_cst[["carrier", "p_nom"]]],
                axis=1,
            )
            .groupby(["bus", "carrier"])
            .sum()
        )
        rhs_cst.index = rhs_cst.index.rename({"bus": "country"})
        rhs_min = agg_p_nom_minmax["min"].dropna()
        idx_min = rhs_min.index.join(rhs_cst.index, how="left")
        rhs_min = rhs_min.reindex(idx_min).fillna(0)
        rhs = (rhs_min - rhs_cst.reindex(idx_min).fillna(0).p_nom).dropna()
        rhs[rhs < 0] = 0
        minimum = xr.DataArray(rhs).rename(dim_0="group")
    else:
        minimum = xr.DataArray(agg_p_nom_minmax["min"].dropna()).rename(dim_0="group")

    index = minimum.indexes["group"].intersection(lhs.indexes["group"])
    if not index.empty:
        n.model.add_constraints(
            lhs.sel(group=index) >= minimum.loc[index], name="agg_p_nom_min"
        )

    if config["solving"]["agg_p_nom_limits"]["include_existing"]:
        rhs_max = agg_p_nom_minmax["max"].dropna()
        idx_max = rhs_max.index.join(rhs_cst.index, how="left")
        rhs_max = rhs_max.reindex(idx_max).fillna(0)
        rhs = (rhs_max - rhs_cst.reindex(idx_max).fillna(0).p_nom).dropna()
        rhs[rhs < 0] = 0
        maximum = xr.DataArray(rhs).rename(dim_0="group")
    else:
        maximum = xr.DataArray(agg_p_nom_minmax["max"].dropna()).rename(dim_0="group")

    index = maximum.indexes["group"].intersection(lhs.indexes["group"])
    if not index.empty:
        n.model.add_constraints(
            lhs.sel(group=index) <= maximum.loc[index], name="agg_p_nom_max"
        )


def add_EQ_constraints(n, o, scaling=1e-1):
    """
    Add equity constraints to the network.

    Currently this is only implemented for the electricity sector only.

    Opts must be specified in the config.yaml.

    Parameters
    ----------
    n : pypsa.Network
    o : str

    Example
    -------
    scenario:
        opts: [Co2L-EQ0.7-24h]

    Require each country or node to on average produce a minimal share
    of its total electricity consumption itself. Example: EQ0.7c demands each country
    to produce on average at least 70% of its consumption; EQ0.7 demands
    each node to produce on average at least 70% of its consumption.
    """
    # TODO: Generalize to cover myopic and other sectors?
    float_regex = "[0-9]*\.?[0-9]+"
    level = float(re.findall(float_regex, o)[0])
    if o[-1] == "c":
        ggrouper = n.generators.bus.map(n.buses.country)
        lgrouper = n.loads.bus.map(n.buses.country)
        sgrouper = n.storage_units.bus.map(n.buses.country)
    else:
        ggrouper = n.generators.bus
        lgrouper = n.loads.bus
        sgrouper = n.storage_units.bus
    load = (
        n.snapshot_weightings.generators
        @ n.loads_t.p_set.groupby(lgrouper, axis=1).sum()
    )
    inflow = (
        n.snapshot_weightings.stores
        @ n.storage_units_t.inflow.groupby(sgrouper, axis=1).sum()
    )
    inflow = inflow.reindex(load.index).fillna(0.0)
    rhs = scaling * (level * load - inflow)
    p = n.model["Generator-p"]
    lhs_gen = (
        (p * (n.snapshot_weightings.generators * scaling))
        .groupby(ggrouper.to_xarray())
        .sum()
        .sum("snapshot")
    )
    # TODO: double check that this is really needed, why do have to subtract the spillage
    if not n.storage_units_t.inflow.empty:
        spillage = n.model["StorageUnit-spill"]
        lhs_spill = (
            (spillage * (-n.snapshot_weightings.stores * scaling))
            .groupby(sgrouper.to_xarray())
            .sum()
            .sum("snapshot")
        )
        lhs = lhs_gen + lhs_spill
    else:
        lhs = lhs_gen
    n.model.add_constraints(lhs >= rhs, name="equity_min")


def add_BAU_constraints(n, config):
    """
    Add a per-carrier minimal overall capacity.

    BAU_mincapacities and opts must be adjusted in the config.yaml.

    Parameters
    ----------
    n : pypsa.Network
    config : dict

    Example
    -------
    scenario:
        opts: [Co2L-BAU-24h]
    electricity:
        BAU_mincapacities:
            solar: 0
            onwind: 0
            OCGT: 100000
            offwind-ac: 0
            offwind-dc: 0
    Which sets minimum expansion across all nodes e.g. in Europe to 100GW.
    OCGT bus 1 + OCGT bus 2 + ... > 100000
    """
    mincaps = pd.Series(config["electricity"]["BAU_mincapacities"])
    p_nom = n.model["Generator-p_nom"]
    ext_i = n.generators.query("p_nom_extendable")
    ext_carrier_i = xr.DataArray(ext_i.carrier.rename_axis("Generator-ext"))
    lhs = p_nom.groupby(ext_carrier_i).sum()
    rhs = mincaps[lhs.indexes["carrier"]].rename_axis("carrier")
    n.model.add_constraints(lhs >= rhs, name="bau_mincaps")


# TODO: think about removing or make per country
def add_SAFE_constraints(n, config):
    """
    Add a capacity reserve margin of a certain fraction above the peak demand.
    Renewable generators and storage do not contribute. Ignores network.

    Parameters
    ----------
        n : pypsa.Network
        config : dict

    Example
    -------
    config.yaml requires to specify opts:

    scenario:
        opts: [Co2L-SAFE-24h]
    electricity:
        SAFE_reservemargin: 0.1
    Which sets a reserve margin of 10% above the peak demand.
    """
    peakdemand = n.loads_t.p_set.sum(axis=1).max()
    margin = 1.0 + config["electricity"]["SAFE_reservemargin"]
    reserve_margin = peakdemand * margin
    conventional_carriers = config["electricity"]["conventional_carriers"]  # noqa: F841
    ext_gens_i = n.generators.query(
        "carrier in @conventional_carriers & p_nom_extendable"
    ).index
    p_nom = n.model["Generator-p_nom"].loc[ext_gens_i]
    lhs = p_nom.sum()
    exist_conv_caps = n.generators.query(
        "~p_nom_extendable & carrier in @conventional_carriers"
    ).p_nom.sum()
    rhs = reserve_margin - exist_conv_caps
    n.model.add_constraints(lhs >= rhs, name="safe_mintotalcap")


def add_green_imports_lim_constraint(n, config):
    """Limit the maximum amount of green fuel imports.

    Add a constraint limiting the maximum amount of green fuel imports
    (i.e. carriers in config["sector"]["green_import_carriers"]) to
    the total amount of green hydrogen production.
    """
    if not config["sector"]["green_imports"]:
        return

    green_import_carriers = [
        f"{c} green import" for c in config["sector"]["green_import_carriers"].keys()
    ]

    green_import_links = n.links.loc[n.links.carrier.isin(green_import_carriers)].index
    if green_import_links.empty:
        return

    # Total green fuel imports
    lhs = (
        n.model["Link-p"].sel(Link=green_import_links).sum("Link")
        * n.snapshot_weightings.generators
    ).sum()

    # Total green hydrogen production
    electrolysis = n.links.loc[n.links.carrier == "H2 Electrolysis"].index

    rhs = (
        (
            n.model["Link-p"].sel(Link=electrolysis)
            * n.links.loc[electrolysis, "efficiency"]
        ).sum("Link")
        * n.snapshot_weightings.generators
    ).sum()

    n.model.add_constraints(lhs <= rhs, name="green_imports_limit")


def add_operational_reserve_margin(n, sns, config):
    """
    Build reserve margin constraints based on the formulation given in
    https://genxproject.github.io/GenX/dev/core/#Reserves.

    Parameters
    ----------
        n : pypsa.Network
        sns: pd.DatetimeIndex
        config : dict

    Example:
    --------
    config.yaml requires to specify operational_reserve:
    operational_reserve: # like https://genxproject.github.io/GenX/dev/core/#Reserves
        activate: true
        epsilon_load: 0.02 # percentage of load at each snapshot
        epsilon_vres: 0.02 # percentage of VRES at each snapshot
        contingency: 400000 # MW
    """
    reserve_config = config["electricity"]["operational_reserve"]
    EPSILON_LOAD = reserve_config["epsilon_load"]
    EPSILON_VRES = reserve_config["epsilon_vres"]
    CONTINGENCY = reserve_config["contingency"]

    # Reserve Variables
    n.model.add_variables(
        0, np.inf, coords=[sns, n.generators.index], name="Generator-r"
    )
    reserve = n.model["Generator-r"]
    summed_reserve = reserve.sum("Generator")

    # Share of extendable renewable capacities
    ext_i = n.generators.query("p_nom_extendable").index
    vres_i = n.generators_t.p_max_pu.columns
    if not ext_i.empty and not vres_i.empty:
        capacity_factor = n.generators_t.p_max_pu[vres_i.intersection(ext_i)]
        p_nom_vres = (
            n.model["Generator-p_nom"]
            .loc[vres_i.intersection(ext_i)]
            .rename({"Generator-ext": "Generator"})
        )
        lhs = summed_reserve + (
            p_nom_vres * (-EPSILON_VRES * xr.DataArray(capacity_factor))
        ).sum("Generator")

    # Total demand per t
    demand = get_as_dense(n, "Load", "p_set").sum(axis=1)

    # VRES potential of non extendable generators
    capacity_factor = n.generators_t.p_max_pu[vres_i.difference(ext_i)]
    renewable_capacity = n.generators.p_nom[vres_i.difference(ext_i)]
    potential = (capacity_factor * renewable_capacity).sum(axis=1)

    # Right-hand-side
    rhs = EPSILON_LOAD * demand + EPSILON_VRES * potential + CONTINGENCY

    n.model.add_constraints(lhs >= rhs, name="reserve_margin")

    # additional constraint that capacity is not exceeded
    gen_i = n.generators.index
    ext_i = n.generators.query("p_nom_extendable").index
    fix_i = n.generators.query("not p_nom_extendable").index

    dispatch = n.model["Generator-p"]
    reserve = n.model["Generator-r"]

    capacity_variable = n.model["Generator-p_nom"].rename(
        {"Generator-ext": "Generator"}
    )
    capacity_fixed = n.generators.p_nom[fix_i]

    p_max_pu = get_as_dense(n, "Generator", "p_max_pu")

    lhs = dispatch + reserve - capacity_variable * xr.DataArray(p_max_pu[ext_i])

    rhs = (p_max_pu[fix_i] * capacity_fixed).reindex(columns=gen_i, fill_value=0)

    n.model.add_constraints(lhs <= rhs, name="Generator-p-reserve-upper")


def add_battery_constraints(n):
    """
    Add constraint ensuring that charger = discharger, i.e.
    1 * charger_size - efficiency * discharger_size = 0
    """
    if not n.links.p_nom_extendable.any():
        return

    discharger_bool = n.links.index.str.contains("battery discharger")
    charger_bool = n.links.index.str.contains("battery charger")

    dischargers_ext = n.links[discharger_bool].query("p_nom_extendable").index
    chargers_ext = n.links[charger_bool].query("p_nom_extendable").index

    eff = n.links.efficiency[dischargers_ext].values
    lhs = (
        n.model["Link-p_nom"].loc[chargers_ext]
        - n.model["Link-p_nom"].loc[dischargers_ext] * eff
    )

    n.model.add_constraints(lhs == 0, name="Link-charger_ratio")


def add_lossy_bidirectional_link_constraints(n):
    if not n.links.p_nom_extendable.any() or "reversed" not in n.links.columns:
        return

    n.links["reversed"] = n.links.reversed.fillna(0).astype(bool)
    carriers = n.links.loc[n.links.reversed, "carrier"].unique()  # noqa: F841

    forward_i = n.links.query(
        "carrier in @carriers and ~reversed and p_nom_extendable"
    ).index

    def get_backward_i(forward_i):
        return pd.Index(
            [
                (
                    re.sub(r"-(\d{4})$", r"-reversed-\1", s)
                    if re.search(r"-\d{4}$", s)
                    else s + "-reversed"
                )
                for s in forward_i
            ]
        )

    backward_i = get_backward_i(forward_i)

    lhs = n.model["Link-p_nom"].loc[backward_i]
    rhs = n.model["Link-p_nom"].loc[forward_i]

    n.model.add_constraints(lhs == rhs, name="Link-bidirectional_sync")


def add_chp_constraints(n):
    electric = (
        n.links.index.str.contains("urban central")
        & n.links.index.str.contains("CHP")
        & n.links.index.str.contains("electric")
    )
    heat = (
        n.links.index.str.contains("urban central")
        & n.links.index.str.contains("CHP")
        & n.links.index.str.contains("heat")
    )

    electric_ext = n.links[electric].query("p_nom_extendable").index
    heat_ext = n.links[heat].query("p_nom_extendable").index

    electric_fix = n.links[electric].query("~p_nom_extendable").index
    heat_fix = n.links[heat].query("~p_nom_extendable").index

    p = n.model["Link-p"]  # dimension: [time, link]

    # output ratio between heat and electricity and top_iso_fuel_line for extendable
    if not electric_ext.empty:
        p_nom = n.model["Link-p_nom"]

        lhs = (
            p_nom.loc[electric_ext]
            * (n.links.p_nom_ratio * n.links.efficiency)[electric_ext].values
            - p_nom.loc[heat_ext] * n.links.efficiency[heat_ext].values
        )
        n.model.add_constraints(lhs == 0, name="chplink-fix_p_nom_ratio")

        rename = {"Link-ext": "Link"}
        lhs = (
            p.loc[:, electric_ext]
            + p.loc[:, heat_ext]
            - p_nom.rename(rename).loc[electric_ext]
        )
        n.model.add_constraints(lhs <= 0, name="chplink-top_iso_fuel_line_ext")

    # top_iso_fuel_line for fixed
    if not electric_fix.empty:
        lhs = p.loc[:, electric_fix] + p.loc[:, heat_fix]
        rhs = n.links.p_nom[electric_fix]
        n.model.add_constraints(lhs <= rhs, name="chplink-top_iso_fuel_line_fix")

    # back-pressure
    if not electric.empty:
        lhs = (
            p.loc[:, heat] * (n.links.efficiency[heat] * n.links.c_b[electric].values)
            - p.loc[:, electric] * n.links.efficiency[electric]
        )
        n.model.add_constraints(lhs <= rhs, name="chplink-backpressure")


def add_pipe_retrofit_constraint(n):
    """
    Add constraint for retrofitting existing CH4 pipelines to H2 pipelines.
    """
    reversed = n.links.reversed if "reversed" in n.links.columns else False
    gas_pipes_i = n.links.query(
        "carrier == 'gas pipeline' and p_nom_extendable and ~@reversed"
    ).index
    h2_retrofitted_i = n.links.query(
        "carrier == 'H2 pipeline retrofitted' and p_nom_extendable and ~@reversed"
    ).index

    if h2_retrofitted_i.empty or gas_pipes_i.empty:
        return

    p_nom = n.model["Link-p_nom"]

    CH4_per_H2 = 1 / n.config["sector"]["H2_retrofit_capacity_per_CH4"]
    lhs = p_nom.loc[gas_pipes_i] + CH4_per_H2 * p_nom.loc[h2_retrofitted_i]
    rhs = n.links.p_nom[gas_pipes_i].rename_axis("Link-ext")

    n.model.add_constraints(lhs == rhs, name="Link-pipe_retrofit")


def add_flexible_egs_constraint(n):
    """
    Upper bounds the charging capacity of the geothermal reservoir according to
    the well capacity.
    """
    well_index = n.links.loc[n.links.carrier == "geothermal heat"].index
    storage_index = n.storage_units.loc[
        n.storage_units.carrier == "geothermal heat"
    ].index

    p_nom_rhs = n.model["Link-p_nom"].loc[well_index]
    p_nom_lhs = n.model["StorageUnit-p_nom"].loc[storage_index]

    n.model.add_constraints(
        p_nom_lhs <= p_nom_rhs,
        name="upper_bound_charging_capacity_of_geothermal_reservoir",
    )


def add_co2_atmosphere_constraint(n, snapshots):
    glcs = n.global_constraints[n.global_constraints.type == "co2_atmosphere"]

    if glcs.empty:
        return
    for name, glc in glcs.iterrows():
        carattr = glc.carrier_attribute
        emissions = n.carriers.query(f"{carattr} != 0")[carattr]

        if emissions.empty:
            continue

        # stores
        bus_carrier = n.stores.bus.map(n.buses.carrier)
        stores = n.stores[bus_carrier.isin(emissions.index) & ~n.stores.e_cyclic]
        if not stores.empty:
            last_i = snapshots[-1]
            lhs = n.model["Store-e"].loc[last_i, stores.index]
            rhs = glc.constant

            n.model.add_constraints(lhs <= rhs, name=f"GlobalConstraint-{name}")


def extra_functionality(n, snapshots):
    """
    Collects supplementary constraints which will be passed to
    ``pypsa.optimization.optimize``.

    If you want to enforce additional custom constraints, this is a good
    location to add them. The arguments ``opts`` and
    ``snakemake.config`` are expected to be attached to the network.
    """
    config = n.config
    constraints = config["solving"].get("constraints", {})
    if constraints["BAU"] and n.generators.p_nom_extendable.any():
        add_BAU_constraints(n, config)
    if constraints["SAFE"] and n.generators.p_nom_extendable.any():
        add_SAFE_constraints(n, config)
    if constraints["CCL"] and n.generators.p_nom_extendable.any():
        add_CCL_constraints(n, config)
    if constraints["green_imports_lim"]:
        add_green_imports_lim_constraint(n, config)

    reserve = config["electricity"].get("operational_reserve", {})
    if reserve.get("activate"):
        add_operational_reserve_margin(n, snapshots, config)

    if EQ_o := constraints["EQ"]:
        add_EQ_constraints(n, EQ_o.replace("EQ", ""))

    if {"solar-hsat", "solar"}.issubset(
        config["electricity"]["renewable_carriers"]
    ) and {"solar-hsat", "solar"}.issubset(
        config["electricity"]["extendable_carriers"]["Generator"]
    ):
        add_solar_potential_constraints(n, config)

    add_battery_constraints(n)
    add_lossy_bidirectional_link_constraints(n)
    add_pipe_retrofit_constraint(n)
    if n._multi_invest:
        add_carbon_constraint(n, snapshots)
        add_carbon_budget_constraint(n, snapshots)
        add_retrofit_gas_boiler_constraint(n, snapshots)
    else:
        add_co2_atmosphere_constraint(n, snapshots)

    if config["sector"]["enhanced_geothermal"]["enable"]:
        add_flexible_egs_constraint(n)

    if n.params.custom_extra_functionality and "snakemake" in globals():
        source_path = n.params.custom_extra_functionality
        assert os.path.exists(source_path), f"{source_path} does not exist"
        sys.path.append(os.path.dirname(source_path))
        module_name = os.path.splitext(os.path.basename(source_path))[0]
        module = importlib.import_module(module_name)
        custom_extra_functionality = getattr(module, module_name)
        custom_extra_functionality(n, snapshots, snakemake)


strategies = dict(
    # The following variables are stored in columns and restored
    # exactly after disaggregation.
    p_nom="sum",
    lifetime="mean",
    # p_nom_max should be infinite if any of summands are infinite
    p_nom_max="sum",
    # Capital cost is taken to be that of the most recent year. Note:
    # components without build year (that are not to be aggregated)
    # will be "trivially" aggregated as 1-element series; in that case
    # their name doesn't end in "-YYYY", hence the check.
    capital_cost=(
        lambda s: (
            s.iloc[pd.Series(s.index.map(lambda x: int(x[-4:]))).idxmax()]
            if len(s) > 1
            else s.squeeze()
        )
    ),
    # Take mean efficiency, then disaggregate. NB: should
    # really be weighted by capacity.
    efficiency="mean",
    efficiency2="mean",
    # Some urban decentral gas boilers have efficiency3 and
    # efficiency4 1.0, other NaN. (mean ignores NaN values).
    efficiency3="mean",
    efficiency4="mean",
    p_nom_min="sum",
    p_nom_extendable=lambda x: x.any(),
    e_nom="sum",
    e_nom_min="sum",
    e_nom_max="sum",
    e_nom_extendable=lambda x: x.any(),
    # length_original sometimes contains NaN values
    length_original="mean",
    # The following two should really be the same, but equality is
    # difficult with floats. (Saving with compression, etc.)
    marginal_cost="mean",
    standing_loss="mean",
    length="mean",
    p_max_pu="mean",
    p_min_pu="mean",
    # Build year is set to 0; to be reset when disaggregating
    build_year=lambda x: 0 if len(x) > 1 else x.squeeze(),
    # "weight" isn't meaningful at this stage; set to 1.
    weight=lambda x: 1,
    # Apparently "control" doesn't really matter; follow
    # pypsa.clustering.spatial by setting to ""
    control=lambda x: "",
    # The "reversed" attribute is sometimes 0, sometimes NaN, which is
    # the only reason for having an aggregation strategy.
    reversed=lambda x: x.any(),
    # The remaining attributes are outputs, and allow the aggregation of solved networks.
    p_nom_opt="sum",
    e_nom_opt="sum",
    p="sum",
    e="sum",
    p0="sum",
    p1="sum",
    p2="sum",
    p3="sum",
    p4="sum",
)

# The following attributes are to be stored by build year in extra
# columns, so that they can be properly disaggregated. The string
# "attr_nom" is replaced by the actual attribute name in the code
# below (i.e. "p_nom", "e_nom", etc.)
vars_to_store = [
    "attr_nom",
    "attr_nom_min",
    "attr_nom_max",
    "attr_nom_extendable",
    "lifetime",
    "capital_cost",
    "marginal_cost",
    "efficiency",
    "efficiency2",
    "efficiency3",
    "efficiency4",
]


def aggregate_build_years(n, exclude_carriers):
    """
    Aggregate components which are identical in all but build year.
    """
    t = time.time()
    indices = dict()

    for c in n.iterate_components():
        # No lines
        if c.name == "Line":
            continue
        if ("build_year" in c.df.columns) and (c.df.build_year > 0).any():
            indices[c.name] = c.df.index.copy()

            attr = nominal_attrs[c.name]

            # Define the aggregation map
            idx_to_agg = c.df.loc[~c.df.carrier.isin(exclude_carriers)].index
            idx_no_year = pd.Series(c.df.index.copy(), index=c.df.index)
            idx_no_year.loc[idx_to_agg] = idx_to_agg.str.replace(
                r"-[0-9]{4}$", "", regex=True
            )

            # For each component (row) in df with name ending in
            # "-YYYY", store the columns listed in `vars_to_store` to
            # be disaggregated again later.
            to_store = []
            for v in [s.replace("attr_nom", attr) for s in vars_to_store]:
                if v not in c.df.columns:
                    continue
                for build_year in c.df.build_year.unique():
                    if build_year == 0:
                        continue
                    mask = c.df.build_year == build_year
                    col = c.df.loc[mask, v].copy()
                    col.index = pd.Index(idx_no_year.loc[mask])
                    col.name = f"{v}-{build_year}"
                    to_store.append(col)

            # For components that are non-extendable, set
            # attr_{min,max} = attr; this is for the aggregated
            # extendable component to have the correct minimum and
            # maximum bounds for nominal capacity.
            non_extendable = c.df[~c.df[f"{attr}_extendable"]].index.intersection(
                idx_to_agg
            )
            c.df.loc[non_extendable, f"{attr}_min"] = c.df.loc[non_extendable, attr]
            c.df.loc[non_extendable, f"{attr}_max"] = c.df.loc[non_extendable, attr]

            # Aggregate
            static_strategies = align_strategies(strategies, c.df.columns, c.name)
            df_aggregated = c.df.groupby(idx_no_year).agg(static_strategies)

            # Add the columns that are stored for disaggregation.
            df_aggregated = pd.concat([df_aggregated] + to_store, axis=1)

            # Aggregate time-varying data.
            pnl_aggregated = Dict()
            dynamic_strategies = align_strategies(strategies, c.pnl, c.name)
            for attr, data in c.pnl.items():
                if data.empty:
                    pnl_aggregated[attr] = data
                    continue

                strategy = dynamic_strategies[attr]
                col_agg_map = idx_no_year.loc[data.columns]
                pnl_aggregated[attr] = data.T.groupby(col_agg_map).agg(strategy).T

            setattr(n, n.components[c.name]["list_name"], df_aggregated)
            setattr(n, n.components[c.name]["list_name"] + "_t", pnl_aggregated)

    logger.info(f"Aggregated build years in {time.time() - t:.1f} seconds")
    return indices


def disaggregate_build_years(n, indices, planning_horizon):
    """
    Disaggregate components which were aggregated by `aggregate_build_years`.
    """
    t = time.time()

    for c in n.iterate_components():
        if c.name in indices:
            attr = nominal_attrs[c.name]
            old_idx = c.df.index.copy()

            # Find the indices of components to be disaggregated
            idx_diff = indices[c.name].difference(c.df.index)

            # Create new DataFrame for all disaggregated components;
            # create column to map to corresponding aggregated
            # component
            disagg_df = pd.DataFrame(index=idx_diff, columns=c.df.columns)
            disagg_df["id_no_year"] = disagg_df.index.str.replace(
                r"-[0-9]{4}$", "", regex=True
            )
            agg_map = disagg_df["id_no_year"].copy()

            # Copy values from aggregated component to disaggregated
            disagg_df.loc[:, c.df.columns] = c.df.loc[disagg_df["id_no_year"]].values

            # Set build year from index
            disagg_df.loc[:, "build_year"] = disagg_df.index.str[-4:].astype(int)

            # Disaggregate specially stored values exactly
            for v in [s.replace("attr_nom", attr) for s in vars_to_store]:
                if v not in c.df.columns:
                    continue
                for build_year in disagg_df.build_year.unique():
                    idx_build_year = disagg_df.build_year == build_year
                    disagg_df.loc[
                        idx_build_year,
                        v,
                    ] = c.df.loc[
                        disagg_df.loc[idx_build_year, "id_no_year"],
                        f"{v}-{build_year}",
                    ].values

            # Set p_nom_opt to p_nom. This should go for all non-extendable
            # disaggregated components. p_nom_opt for the last planning horizon
            # is dealt with below.
            disagg_df.loc[:, f"{attr}_opt"] = disagg_df.loc[:, attr]

            # Handle the last planning horizon (which was just
            # optimised) specifically: we have to subtract the sum of
            # nominal capacities of the previous years from attr_opt.
            idx_last_horizon = disagg_df.loc[
                disagg_df.build_year == int(planning_horizon)
            ].index
            disagg_df.loc[idx_last_horizon, f"{attr}_opt"] = c.df.loc[
                disagg_df.loc[idx_last_horizon, "id_no_year"], f"{attr}_opt"
            ].values
            years = c.df.columns.str.extract(rf"{attr}-(\d{{4}})$").dropna()[0]
            prev_years = years[years.astype(int) < int(planning_horizon)]
            disagg_df.loc[idx_last_horizon, f"{attr}_opt"] -= (
                c.df.loc[
                    disagg_df.loc[idx_last_horizon, "id_no_year"],
                    [f"{attr}-{p}" for p in prev_years.values],
                ]
                .sum(axis=1)
                .values
            )

            # Also make last year extendable again
            disagg_df.loc[idx_last_horizon, f"{attr}_extendable"] = True

            # Drop auxiliary column keeping track of aggregated component
            disagg_df.drop("id_no_year", axis=1, inplace=True)

            # Add disaggregated components to c.df. Watch out: c.df
            # will still refer to the "old" dataframe.
            import_components_from_dataframe(n, disagg_df, c.name)

            # Also duplicate the corresponding column in pnl
            for v in c.pnl:
                if c.pnl[v].empty:
                    continue

                # Set the new columns to the values of the old columns
                mask = agg_map.index[agg_map.isin(c.pnl[v].columns)]
                pnl = c.pnl[v].loc[:, agg_map[mask]]
                pnl.columns = mask
                import_series_from_dataframe(n, pnl, c.name, v)

                # Variables that are outputs and don't start with
                # "mu_" need to be scaled by nominal capacity.
                if (
                    n.components[c.name]["attrs"].loc[v, "status"] == "Output"
                ) and not (v.startswith("mu_")):
                    scaling_factors = (
                        (
                            disagg_df.loc[mask, attr]
                            / c.df.loc[agg_map[mask], attr].replace(0.0, np.nan).values
                        )
                        .astype(float)
                        .fillna(0.0)
                        .reindex(c.pnl[v].columns, fill_value=1.0)
                    )
                    # For extendable components, recompute the scaling
                    # factors using attr + "_opt"
                    ext_i = mask[c.df.loc[agg_map[mask], f"{attr}_extendable"]]
                    scaling_factors.loc[ext_i] = (
                        (
                            disagg_df.loc[ext_i, f"{attr}_opt"]
                            / c.df.loc[agg_map[ext_i], f"{attr}_opt"]
                            .replace(0.0, np.nan)
                            .values
                        )
                        .astype(float)
                        .fillna(0.0)
                    )
                    c.pnl[v] = c.pnl[v].mul(scaling_factors, axis=1)

            # Drop all columns in df ending in "-YYYY" (the columns
            # used to track aggregated information that has now been
            # disaggregated).
            cols_to_drop = n.df(c.name).columns[
                n.df(c.name).columns.str.match(r".*-[0-9]{4}$")
            ]
            n.df(c.name).drop(cols_to_drop, axis=1, inplace=True)

            # Now remove the aggregated components from both static
            # and varying data.
            n.df(c.name).drop(old_idx.difference(indices[c.name]), inplace=True)
            for _, data in c.pnl.items():
                if data.empty:
                    continue
                data.drop(
                    old_idx.difference(indices[c.name]).intersection(data.columns),
                    axis=1,
                    inplace=True,
                )

    # Fix problem with boolean values in "reversed" column
    if "reversed" in n.links.columns:
        n.links.reversed = n.links.reversed.astype(float)

    logger.info(f"Disaggregated build years in {time.time() - t:.1f} seconds")


def solve_network(
    n, config, params, solving, build_year_agg, rule, current_horizon, **kwargs
):
    set_of_options = solving["solver"]["options"]
    cf_solving = solving["options"]

    kwargs["multi_investment_periods"] = config["foresight"] == "perfect"
    kwargs["solver_options"] = (
        solving["solver_options"][set_of_options] if set_of_options else {}
    )
    kwargs["solver_name"] = solving["solver"]["name"]
    kwargs["extra_functionality"] = extra_functionality
    kwargs["transmission_losses"] = cf_solving.get("transmission_losses", False)
    kwargs["linearized_unit_commitment"] = cf_solving.get(
        "linearized_unit_commitment", False
    )
    kwargs["assign_all_duals"] = cf_solving.get("assign_all_duals", False)
    kwargs["io_api"] = cf_solving.get("io_api", None)

    if kwargs["solver_name"] == "gurobi":
        logging.getLogger("gurobipy").setLevel(logging.CRITICAL)

    if "model_options" in solving:
        kwargs["model_kwargs"] = solving["model_options"]

        if (
            "solver_dir" in kwargs["model_kwargs"]
            and "$" in kwargs["model_kwargs"]["solver_dir"]
        ):
            # Resolve env var as path
            kwargs["model_kwargs"]["solver_dir"] = os.path.expandvars(
                kwargs["model_kwargs"]["solver_dir"]
            )
            logger.info(f"Set solver_dir to {kwargs['model_kwargs']['solver_dir']}")

    rolling_horizon = cf_solving.pop("rolling_horizon", False)
    skip_iterations = cf_solving.pop("skip_iterations", False)
    if not n.lines.s_nom_extendable.any():
        skip_iterations = True
        logger.info("No expandable lines found. Skipping iterative solving.")

    # add to network for extra_functionality
    n.config = config
    n.params = params

    build_year_agg_enabled = build_year_agg["enable"] and (
        config["foresight"] == "myopic"
    )
    if build_year_agg_enabled:
        indices = aggregate_build_years(
            n, exclude_carriers=build_year_agg["exclude_carriers"]
        )

    if rolling_horizon and rule == "solve_operations_network":
        kwargs["horizon"] = cf_solving.get("horizon", 365)
        kwargs["overlap"] = cf_solving.get("overlap", 0)
        n.optimize.optimize_with_rolling_horizon(**kwargs)
        status, condition = "", ""
    elif skip_iterations:
        status, condition = n.optimize(**kwargs)
    else:
        kwargs["track_iterations"] = cf_solving["track_iterations"]
        kwargs["min_iterations"] = cf_solving["min_iterations"]
        kwargs["max_iterations"] = cf_solving["max_iterations"]
        if cf_solving["post_discretization"].pop("enable"):
            logger.info("Add post-discretization parameters.")
            kwargs.update(cf_solving["post_discretization"])
        status, condition = n.optimize.optimize_transmission_expansion_iteratively(
            **kwargs
        )

    if status != "ok" and not rolling_horizon:
        logger.warning(
            f"Solving status '{status}' with termination condition '{condition}'"
        )
    if "infeasible" in condition:
        labels = n.model.compute_infeasibilities()
        logger.info(f"Labels:\n{labels}")
        n.model.print_infeasibilities()
        raise RuntimeError("Solving status 'infeasible'")

    if build_year_agg_enabled:
        disaggregate_build_years(n, indices, current_horizon)

    return n


# %%
if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake(
            "solve_sector_network_perfect",
            configfiles="../config/test/config.perfect.yaml",
            opts="",
            clusters="5",
            ll="v1.0",
            sector_opts="",
            # planning_horizons="2030",
        )
    configure_logging(snakemake)
    set_scenario_config(snakemake)
    update_config_from_wildcards(snakemake.config, snakemake.wildcards)

    solve_opts = snakemake.params.solving["options"]

    np.random.seed(solve_opts.get("seed", 123))

    n = pypsa.Network(snakemake.input.network)

    n = prepare_network(
        n,
        solve_opts,
        clusters=snakemake.wildcards.clusters,
        config=snakemake.config,
        sector=snakemake.params.sector,
        foresight=snakemake.params.foresight,
        planning_horizons=snakemake.params.planning_horizons,
        current_horizon=snakemake.wildcards.planning_horizons,
    )

    n.custom_extra_functionality = snakemake.params.custom_extra_functionality

    with memory_logger(
        filename=getattr(snakemake.log, "memory", None), interval=30.0
    ) as mem:
        n = solve_network(
            n,
            config=snakemake.config,
            params=snakemake.params,
            solving=snakemake.params.solving,
            build_year_agg=snakemake.params.get("build_year_agg", {"enable": False}),
            rule=snakemake.rule,
            current_horizon=snakemake.wildcards.planning_horizons,
            log_fn=snakemake.log.solver,
        )

    logger.info(f"Maximum memory usage: {mem.mem_usage}")

    n.meta = dict(snakemake.config, **dict(wildcards=dict(snakemake.wildcards)))
    n.export_to_netcdf(snakemake.output.network)

    with open(snakemake.output.config, "w") as file:
        yaml.dump(
            n.meta,
            file,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
