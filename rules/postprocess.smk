# SPDX-FileCopyrightText: : 2023-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT


if config["foresight"] != "perfect":

    rule plot_power_network_clustered:
        params:
            plotting=config_provider("plotting"),
        input:
            network=resources("networks/base_s_{clusters}.nc"),
            regions_onshore=resources("regions_onshore_base_s_{clusters}.geojson"),
        output:
            map=resources("maps/power-network-s-{clusters}.pdf"),
        threads: 1
        resources:
            mem_mb=4000,
        benchmark:
            benchmarks("plot_power_network_clustered/base_s_{clusters}")
        conda:
            "../envs/environment.yaml"
        script:
            "../scripts/plot_power_network_clustered.py"

    rule plot_power_network:
        params:
            plotting=config_provider("plotting"),
        input:
            network=RESULTS
            + "postnetworks/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}.nc",
            regions=resources("regions_onshore_base_s_{clusters}.geojson"),
        output:
            map=RESULTS
            + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-costs-all_{planning_horizons}{near_opt}.pdf",
        threads: 2
        resources:
            mem_mb=10000,
        log:
            RESULTS
            + "logs/plot_power_network/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}.log",
        benchmark:
            (
                RESULTS
                + "benchmarks/plot_power_network/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}"
            )
        conda:
            "../envs/environment.yaml"
        script:
            "../scripts/plot_power_network.py"

    rule plot_hydrogen_network:
        params:
            plotting=config_provider("plotting"),
            foresight=config_provider("foresight"),
        input:
            network=RESULTS
            + "postnetworks/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}.nc",
            regions=resources("regions_onshore_base_s_{clusters}.geojson"),
        output:
            map=RESULTS
            + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-h2_network_{planning_horizons}{near_opt}.pdf",
        threads: 2
        resources:
            mem_mb=10000,
        log:
            RESULTS
            + "logs/plot_hydrogen_network/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}.log",
        benchmark:
            (
                RESULTS
                + "benchmarks/plot_hydrogen_network/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}"
            )
        conda:
            "../envs/environment.yaml"
        script:
            "../scripts/plot_hydrogen_network.py"

    rule plot_gas_network:
        params:
            plotting=config_provider("plotting"),
        input:
            network=RESULTS
            + "postnetworks/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}.nc",
            regions=resources("regions_onshore_base_s_{clusters}.geojson"),
        output:
            map=RESULTS
            + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-ch4_network_{planning_horizons}{near_opt}.pdf",
        threads: 2
        resources:
            mem_mb=10000,
        log:
            RESULTS
            + "logs/plot_gas_network/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}.log",
        benchmark:
            (
                RESULTS
                + "benchmarks/plot_gas_network/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}{near_opt}"
            )
        conda:
            "../envs/environment.yaml"
        script:
            "../scripts/plot_gas_network.py"


if config["foresight"] == "perfect":

    def output_map_year(w):
        return {
            f"map_{year}": RESULTS
            + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-costs-all_"
            + f"{year}.pdf"
            for year in config_provider("scenario", "planning_horizons")(w)
        }

    rule plot_power_network_perfect:
        params:
            plotting=config_provider("plotting"),
        input:
            network=RESULTS
            + "postnetworks/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_brownfield_all_years.nc",
            regions=resources("regions_onshore_base_s_{clusters}.geojson"),
        output:
            unpack(output_map_year),
        threads: 2
        resources:
            mem_mb=10000,
        conda:
            "../envs/environment.yaml"
        script:
            "../scripts/plot_power_network_perfect.py"


rule make_summary:
    params:
        foresight=config_provider("foresight"),
        costs=config_provider("costs"),
        snapshots=config_provider("snapshots"),
        drop_leap_day=config_provider("enable", "drop_leap_day"),
        scenario=config_provider("scenario"),
        RDIR=RDIR,
    input:
        networks=expand(
            RESULTS
            + "postnetworks/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}.nc",
            **config["scenario"],
            allow_missing=True,
        ),
        costs=lambda w: (
            resources("costs_{}.csv".format(config_provider("costs", "year")(w)))
            if config_provider("foresight")(w) == "overnight"
            else resources(
                "costs_{}.csv".format(
                    config_provider("scenario", "planning_horizons", 0)(w)
                )
            )
        ),
        ac_plot=expand(
            resources("maps/power-network-s-{clusters}.pdf"),
            **config["scenario"],
            allow_missing=True,
        ),
        costs_plot=expand(
            RESULTS
            + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-costs-all_{planning_horizons}.pdf",
            **config["scenario"],
            allow_missing=True,
        ),
        h2_plot=lambda w: expand(
            (
                RESULTS
                + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-h2_network_{planning_horizons}.pdf"
                if config_provider("sector", "H2_network")(w)
                else []
            ),
            **config["scenario"],
            allow_missing=True,
        ),
        ch4_plot=lambda w: expand(
            (
                RESULTS
                + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-ch4_network_{planning_horizons}.pdf"
                if config_provider("sector", "gas_network")(w)
                else []
            ),
            **config["scenario"],
            allow_missing=True,
        ),
    output:
        nodal_costs=RESULTS + "csvs/nodal_costs.csv",
        nodal_capacities=RESULTS + "csvs/nodal_capacities.csv",
        nodal_cfs=RESULTS + "csvs/nodal_cfs.csv",
        cfs=RESULTS + "csvs/cfs.csv",
        costs=RESULTS + "csvs/costs.csv",
        capacities=RESULTS + "csvs/capacities.csv",
        curtailment=RESULTS + "csvs/curtailment.csv",
        energy=RESULTS + "csvs/energy.csv",
        supply=RESULTS + "csvs/supply.csv",
        supply_energy=RESULTS + "csvs/supply_energy.csv",
        nodal_supply_energy=RESULTS + "csvs/nodal_supply_energy.csv",
        prices=RESULTS + "csvs/prices.csv",
        weighted_prices=RESULTS + "csvs/weighted_prices.csv",
        market_values=RESULTS + "csvs/market_values.csv",
        price_statistics=RESULTS + "csvs/price_statistics.csv",
        metrics=RESULTS + "csvs/metrics.csv",
    threads: 2
    resources:
        mem_mb=10000,
    log:
        RESULTS + "logs/make_summary.log",
    conda:
        "../envs/environment.yaml"
    localrule: True
    script:
        "../scripts/make_summary.py"


rule make_summary_near_opt:
    params:
        foresight=config_provider("foresight"),
        costs=config_provider("costs"),
        snapshots=config_provider("snapshots"),
        drop_leap_day=config_provider("enable", "drop_leap_day"),
        scenario=config_provider("scenario"),
        RDIR=RDIR,
    input:
        networks=expand(
            RESULTS
            + "postnetworks/base_s_{clusters}_l{ll}_{opts}_{sector_opts}_{planning_horizons}_{sense}{slack}.nc",
            **config["scenario"],
            sense=["min", "max"],
            allow_missing=True,
        ),
        costs=lambda w: (
            resources("costs_{}.csv".format(config_provider("costs", "year")(w)))
            if config_provider("foresight")(w) == "overnight"
            else resources(
                "costs_{}.csv".format(
                    config_provider("scenario", "planning_horizons", 0)(w)
                )
            )
        ),
        ac_plot=expand(
            resources("maps/power-network-s-{clusters}.pdf"),
            **config["scenario"],
            allow_missing=True,
        ),
        costs_plot=expand(
            RESULTS
            + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-costs-all_{planning_horizons}_{sense}{slack}.pdf",
            **config["scenario"],
            sense=["min", "max"],
            allow_missing=True,
        ),
        h2_plot=lambda w: expand(
            (
                RESULTS
                + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-h2_network_{planning_horizons}_{sense}{slack}.pdf"
                if config_provider("sector", "H2_network")(w)
                else []
            ),
            **config["scenario"],
            sense=["min", "max"],
            allow_missing=True,
        ),
        ch4_plot=lambda w: expand(
            (
                RESULTS
                + "maps/base_s_{clusters}_l{ll}_{opts}_{sector_opts}-ch4_network_{planning_horizons}_{sense}{slack}.pdf"
                if config_provider("sector", "gas_network")(w)
                else []
            ),
            **config["scenario"],
            sense=["min", "max"],
            allow_missing=True,
        ),
    output:
        nodal_costs=RESULTS + "csvs_near_opt/nodal_costs.csv",
        nodal_capacities=RESULTS + "csvs_near_opt/nodal_capacities.csv",
        nodal_cfs=RESULTS + "csvs_near_opt/nodal_cfs.csv",
        cfs=RESULTS + "csvs_near_opt/cfs.csv",
        costs=RESULTS + "csvs_near_opt/costs.csv",
        capacities=RESULTS + "csvs_near_opt/capacities.csv",
        curtailment=RESULTS + "csvs_near_opt/curtailment.csv",
        energy=RESULTS + "csvs_near_opt/energy.csv",
        supply=RESULTS + "csvs_near_opt/supply.csv",
        supply_energy=RESULTS + "csvs_near_opt/supply_energy.csv",
        nodal_supply_energy=RESULTS + "csvs_near_opt/nodal_supply_energy.csv",
        prices=RESULTS + "csvs_near_opt/prices.csv",
        weighted_prices=RESULTS + "csvs_near_opt/weighted_prices.csv",
        market_values=RESULTS + "csvs_near_opt/market_values.csv",
        price_statistics=RESULTS + "csvs_near_opt/price_statistics.csv",
        metrics=RESULTS + "csvs_near_opt/metrics.csv",
    threads: 2
    resources:
        mem_mb=10000,
    log:
        RESULTS + "logs/make_summary_near_opt.log",
    conda:
        "../envs/environment.yaml"
    localrule: True
    script:
        "../scripts/make_summary_near_opt.py"


rule plot_summary:
    params:
        countries=config_provider("countries"),
        planning_horizons=config_provider("scenario", "planning_horizons"),
        emissions_scope=config_provider("energy", "emissions"),
        plotting=config_provider("plotting"),
        foresight=config_provider("foresight"),
        co2_budget=config_provider("co2_budget"),
        sector=config_provider("sector"),
        RDIR=RDIR,
    input:
        costs=RESULTS + "csvs/costs.csv",
        energy=RESULTS + "csvs/energy.csv",
        balances=RESULTS + "csvs/supply_energy.csv",
        eurostat="data/eurostat/Balances-April2023",
        co2="data/bundle/eea/UNFCCC_v23.csv",
    output:
        costs=RESULTS + "graphs/costs.svg",
        energy=RESULTS + "graphs/energy.svg",
        balances=RESULTS + "graphs/balances-energy.svg",
    threads: 2
    resources:
        mem_mb=10000,
    log:
        RESULTS + "logs/plot_summary.log",
    conda:
        "../envs/environment.yaml"
    localrule: True
    script:
        "../scripts/plot_summary.py"


rule plot_summary_near_opt:
    params:
        countries=config_provider("countries"),
        planning_horizons=config_provider("scenario", "planning_horizons"),
        emissions_scope=config_provider("energy", "emissions"),
        plotting=config_provider("plotting"),
        foresight=config_provider("foresight"),
        co2_budget=config_provider("co2_budget"),
        sector=config_provider("sector"),
        RDIR=RDIR,
    input:
        costs=RESULTS + "csvs_near_opt/costs.csv",
        energy=RESULTS + "csvs_near_opt/energy.csv",
        balances=RESULTS + "csvs_near_opt/supply_energy.csv",
        eurostat="data/eurostat/eurostat-energy_balances-april_2023_edition",
        co2="data/bundle/eea/UNFCCC_v23.csv",
    output:
        costs=RESULTS + "graphs_near_opt/costs.svg",
        energy=RESULTS + "graphs_near_opt/energy.svg",
        balances=RESULTS + "graphs_near_opt/balances-energy.svg",
    threads: 2
    resources:
        mem_mb=10000,
    log:
        RESULTS + "logs/plot_summary_near_opt.log",
    conda:
        "../envs/environment.yaml"
    localrule: True
    script:
        "../scripts/plot_summary_near_opt.py"


rule make_all_summaries:
    input:
        expand(RESULTS + "csvs/costs.csv", run=config["run"]["name"]),
        expand(RESULTS + "csvs_near_opt/costs.csv", run=config["run"]["name"]),


rule plot_all_summaries:
    input:
        expand(RESULTS + "graphs/costs.svg", run=config["run"]["name"]),
        expand(RESULTS + "graphs_near_opt/costs.svg", run=config["run"]["name"]),


STATISTICS_BARPLOTS = [
    "capacity_factor",
    "installed_capacity",
    "optimal_capacity",
    "capital_expenditure",
    "operational_expenditure",
    "curtailment",
    "supply",
    "withdrawal",
    "market_value",
]


rule plot_base_statistics:
    params:
        plotting=config_provider("plotting"),
        barplots=STATISTICS_BARPLOTS,
    input:
        network=RESULTS + "networks/base_s_{clusters}_elec_l{ll}_{opts}.nc",
    output:
        **{
            f"{plot}_bar": RESULTS
            + f"figures/statistics_{plot}_bar_base_s_{{clusters}}_elec_l{{ll}}_{{opts}}.pdf"
            for plot in STATISTICS_BARPLOTS
        },
        barplots_touch=RESULTS
        + "figures/.statistics_plots_base_s_{clusters}_elec_l{ll}_{opts}",
    script:
        "../scripts/plot_statistics.py"
