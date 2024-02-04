# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2020-2024 The PyPSA-Eur Authors
#
# SPDX-License-Identifier: MIT
"""
Creates plots from summary CSV files.
"""

import logging

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import pandas as pd
from _helpers import configure_logging, set_scenario_config
from plot_summary import (
    plot_balances,
    plot_carbon_budget_distribution,
    plot_costs,
    plot_energy,
)
from prepare_sector_network import co2_emissions_year

logger = logging.getLogger(__name__)
plt.style.use("ggplot")


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        snakemake = mock_snakemake("plot_summary")

    configure_logging(snakemake)
    set_scenario_config(snakemake)

    n_header = 6

    plot_costs(snakemake, n_header)

    plot_energy(snakemake, n_header)

    plot_balances(snakemake, n_header)
