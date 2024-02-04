# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2020-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Create summary CSV files for all scenario runs including costs, capacities,
capacity factors, curtailment, energy balances, prices and other metrics.
"""

import logging
import sys

import numpy as np
import pandas as pd
import pypsa
from _helpers import configure_logging, get_snapshots, set_scenario_config
from make_summary import (
    assign_carriers,
    assign_locations,
    calculate_capacities,
    calculate_cfs,
    calculate_costs,
    calculate_curtailment,
    calculate_energy,
    calculate_market_values,
    calculate_metrics,
    calculate_nodal_capacities,
    calculate_nodal_cfs,
    calculate_nodal_costs,
    calculate_nodal_supply_energy,
    calculate_price_statistics,
    calculate_prices,
    calculate_supply,
    calculate_supply_energy,
    calculate_weighted_prices,
    configure_logging,
    get_snapshots,
    prepare_costs,
    set_scenario_config,
)
from prepare_sector_network import prepare_costs

idx = pd.IndexSlice
logger = logging.getLogger(__name__)
opt_name = {"Store": "e", "Line": "s", "Transformer": "s"}


def calculate_cumulative_cost():
    planning_horizons = snakemake.params.scenario["planning_horizons"]

    cumulative_cost = pd.DataFrame(
        index=df["costs"].sum().index,
        columns=pd.Series(data=np.arange(0, 0.1, 0.01), name="social discount rate"),
    )

    # discount cost and express them in money value of planning_horizons[0]
    for r in cumulative_cost.columns:
        cumulative_cost[r] = [
            df["costs"].sum()[index] / ((1 + r) ** (index[-1] - planning_horizons[0]))
            for index in cumulative_cost.index
        ]

    # integrate cost throughout the transition path
    for r in cumulative_cost.columns:
        for cluster in cumulative_cost.index.get_level_values(level=0).unique():
            for ll in cumulative_cost.index.get_level_values(level=1).unique():
                for sector_opts in cumulative_cost.index.get_level_values(
                    level=2
                ).unique():
                    for sense in cumulative_cost.index.get_level_values(
                        level=4
                    ).unique():
                        for slack in cumulative_cost.index.get_level_values(
                            level=5
                        ).unique():
                            cumulative_cost.loc[
                                (
                                    cluster,
                                    ll,
                                    sector_opts,
                                    "cumulative cost",
                                    sense,
                                    slack,
                                ),
                                r,
                            ] = np.trapz(
                                cumulative_cost.loc[
                                    idx[
                                        cluster,
                                        ll,
                                        sector_opts,
                                        planning_horizons,
                                        sense,
                                        slack,
                                    ],
                                    r,
                                ].values,
                                x=planning_horizons,
                            )

    return cumulative_cost


def make_summaries(networks_dict):
    outputs = [
        "nodal_costs",
        "nodal_capacities",
        "nodal_cfs",
        "cfs",
        "costs",
        "capacities",
        "curtailment",
        "energy",
        "supply",
        "supply_energy",
        "nodal_supply_energy",
        "prices",
        "weighted_prices",
        "price_statistics",
        "market_values",
        "metrics",
    ]

    columns = pd.MultiIndex.from_tuples(
        networks_dict.keys(),
        names=["cluster", "ll", "opt", "planning_horizon", "sense", "slack"],
    )

    df = {output: pd.DataFrame(columns=columns, dtype=float) for output in outputs}
    for label, filename in networks_dict.items():
        logger.info(f"Make summary for scenario {label}, using {filename}")

        n = pypsa.Network(filename)

        assign_carriers(n)
        assign_locations(n)

        for output in outputs:
            df[output] = globals()["calculate_" + output](n, label, df[output])

    return df


def to_csv(df):
    for key in df:
        df[key].to_csv(snakemake.output[key])


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake("make_summary")

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    networks_dict = {
        (cluster, ll, opt + sector_opt, planning_horizon, sense, slack): "results/"
        + snakemake.params.RDIR
        + f"/postnetworks/base_s_{cluster}_l{ll}_{opt}_{sector_opt}_{planning_horizon}_{sense}{slack}.nc"
        for cluster in snakemake.params.scenario["clusters"]
        for opt in snakemake.params.scenario["opts"]
        for sector_opt in snakemake.params.scenario["sector_opts"]
        for ll in snakemake.params.scenario["ll"]
        for planning_horizon in snakemake.params.scenario["planning_horizons"]
        for sense in ["min", "max"]
        for slack in snakemake.params.scenario["slack"]
    }

    time = get_snapshots(snakemake.params.snapshots, snakemake.params.drop_leap_day)
    Nyears = len(time) / 8760

    costs_db = prepare_costs(
        snakemake.input.costs,
        snakemake.params.costs,
        Nyears,
    )

    df = make_summaries(networks_dict)

    df["metrics"].loc["total costs"] = df["costs"].sum()

    to_csv(df)

    if snakemake.params.foresight == "myopic":
        cumulative_cost = calculate_cumulative_cost()
        cumulative_cost.to_csv(
            "results/" + snakemake.params.RDIR + "csvs/cumulative_cost.csv"
        )
