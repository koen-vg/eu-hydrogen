# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2023-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT

# This script helps to generate a scenarios.yaml file for PyPSA-Eur.
# You can modify the template to your needs and define all possible combinations of config values that should be considered.

if "snakemake" in globals():
    filename = snakemake.output[0]
else:
    filename = "../config/scenarios.yaml"

import itertools

filename_stripped = filename.split("/")[-1]

if filename_stripped == "scenarios.yaml":
    template = """
    {scenario_name}:
        snapshots:
            start: "{year}-01-01 00:00"
            end: "{year}-12-31 23:00"
            inclusive: "both"
        renewable:
            onwind:
                cutout: europe-era5_{year}
            solar:
                cutout: europe-era5_{year}
            offwind-ac:
                cutout: europe-era5_{year}
            offwind-dc:
                cutout: europe-era5_{year}
            offwind-float:
                cutout: europe-era5_{year}
            hydro:
                cutout: europe-era5_{year}
        solar_thermal:
            cutout: europe-era5_{year}
        lines:
            dynamic_line_rating:
                cutout: europe-era5_{year}
        sector:
            heat_demand_cutout: europe-era5_{year}
        biomass:
            scenario: {biomass_scenario}
    """

    scenarios = {
        "Y": {
            "1987": {
                "year": "1987",
            },
            # "2013": {
            #     "year": "2013",
            # },
            "2020": {
                "year": "2020",
            },
        },
        "B": {
            "a": {
                "biomass_scenario": "ENS_Low",
            },
            "b": {
                "biomass_scenario": "ENS_Med",
            },
            "c": {
                "biomass_scenario": "ENS_High",
            },
        },
    }

# Create set product of all scenario outcomes
scenario_levels = {k: list(v.keys()) for k, v in scenarios.items()}
scenario_levels = [[(k, v) for v in levels] for k, levels in scenario_levels.items()]
combinations = list(itertools.product(*scenario_levels))


def merge_dicts(list_of_dicts):
    return {k: v for d in list_of_dicts for k, v in d.items()}


config_combinations = [
    merge_dicts([scenarios[s][l] for (s, l) in c]) for c in combinations
]

with open(filename, "w") as f:
    for c, config in zip(combinations, config_combinations):
        # Construct name string from c
        name = "_".join([f"{s}{l}" for s, l in c])
        f.write(template.format(scenario_name=name, **config))
