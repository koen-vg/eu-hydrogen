..
  SPDX-FileCopyrightText: 2019-2024 The PyPSA-Eur Authors

  SPDX-License-Identifier: CC-BY-4.0

.. _config:

##########################################
Configuration
##########################################

PyPSA-Eur has several configuration options which are documented in this section and are collected in a ``config/config.yaml`` file. This file defines deviations from the default configuration (``config/config.default.yaml``); confer installation instructions at :ref:`defaultconfig`.

.. _toplevel_cf:

Top-level configuration
=======================

"Private" refers to local, machine-specific settings or data meant for personal use, not to be shared. "Remote" indicates the address of a server used for data exchange, often for clusters and data pushing/pulling.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: version:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/toplevel.csv

.. _run_cf:

``run``
=============

It is common conduct to analyse energy system optimisation models for **multiple scenarios** for a variety of reasons,
e.g. assessing their sensitivity towards changing the temporal and/or geographical resolution or investigating how
investment changes as more ambitious greenhouse-gas emission reduction targets are applied.

The ``run`` section is used for running and storing scenarios with different configurations which are not covered by :ref:`wildcards`. It determines the path at which resources, networks and results are stored. Therefore the user can run different configurations within the same directory. If a run with a non-empty name should use cutouts shared across runs, set ``shared_cutouts`` to `true`.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: run:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/run.csv

.. _foresight_cf:

``foresight``
=============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: foresight:
   :end-at: foresight:

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/foresight.csv

.. note::
    If you use myopic or perfect foresight, the planning horizon in
    :ref:`planning_horizons` in scenario has to be set.

.. _scenario:

``scenario``
============

The ``scenario`` section is an extraordinary section of the config file
that is strongly connected to the :ref:`wildcards` and is designed to
facilitate running multiple scenarios through a single command

.. code:: bash

   # for electricity-only studies
   snakemake -call solve_elec_networks

   # for sector-coupling studies
   snakemake -call solve_sector_networks

For each wildcard, a **list of values** is provided. The rule
``solve_all_elec_networks`` will trigger the rules for creating
``results/networks/base_s_{clusters}_elec_l{ll}_{opts}.nc`` for **all
combinations** of the provided wildcard values as defined by Python's
`itertools.product(...)
<https://docs.python.org/2/library/itertools.html#itertools.product>`__ function
that snakemake's `expand(...) function
<https://snakemake.readthedocs.io/en/stable/snakefiles/rules.html#targets>`__
uses.

An exemplary dependency graph (starting from the simplification rules) then looks like this:

.. image:: img/scenarios.png

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: scenario:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/scenario.csv

.. _countries:

``countries``
=============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: countries:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/countries.csv

.. _snapshots_cf:

``snapshots``
=============

Specifies the temporal range to build an energy system model for as arguments to `pandas.date_range <https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.date_range.html>`__

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: snapshots:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/snapshots.csv

.. _enable_cf:

``enable``
==========

Switches for some rules and optional features.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: enable:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/enable.csv

.. _CO2_budget_cf:

``co2 budget``
==============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: co2_budget:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/co2_budget.csv

.. note::
    this parameter is over-ridden if ``Co2Lx`` or ``cb`` is set in
    sector_opts.

.. _electricity_cf:

``electricity``
===============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: electricity:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/electricity.csv

.. _atlite_cf:

``atlite``
==========

Define and specify the ``atlite.Cutout`` used for calculating renewable potentials and time-series. All options except for ``features`` are directly used as `cutout parameters <https://atlite.readthedocs.io/en/latest/ref_api.html#cutout>`__.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: atlite:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/atlite.csv

.. _renewable_cf:

``renewable``
=============

``onwind``
----------

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: renewable:
   :end-before:   offwind-ac:

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/onwind.csv

.. note::
   Notes on ``capacity_per_sqkm``. ScholzPhd Tab 4.3.1: 10MW/km^2 and assuming 30% fraction of the already restricted
   area is available for installation of wind generators due to competing land use and likely public
   acceptance issues.

.. note::
   The default choice for corine ``grid_codes`` was based on Scholz, Y. (2012). Renewable energy based electricity supply at low costs
   development of the REMix model and application for Europe. ( p.42 / p.28)

``offwind-ac``
--------------

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at:   offwind-ac:
   :end-before:   offwind-dc:

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/offwind-ac.csv

.. note::
   Notes on ``capacity_per_sqkm``. ScholzPhd Tab 4.3.1: 10MW/km^2 and assuming 20% fraction of the already restricted
   area is available for installation of wind generators due to competing land use and likely public
   acceptance issues.

.. note::
   Notes on ``correction_factor``. Correction due to proxy for wake losses
   from 10.1016/j.energy.2018.08.153
   until done more rigorously in #153

``offwind-dc``
---------------

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at:   offwind-dc:
   :end-before:   offwind-float:

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/offwind-dc.csv

.. note::
   Both ``offwind-ac`` and ``offwind-dc`` have the same assumption on
   ``capacity_per_sqkm`` and ``correction_factor``.

``offwind-float``
---------------

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at:   offwind-float:
   :end-before:   solar:

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/offwind-float.csv

.. note::
   ``offwind-ac``,  ``offwind-dc`` , ``offwind-float`` have the same assumption on
   ``capacity_per_sqkm`` and ``correction_factor``.
``solar``
---------------

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at:   solar:
   :end-before:   hydro:

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/solar.csv

.. note::
   Notes on ``capacity_per_sqkm``. ScholzPhd Tab 4.3.1: 170 MW/km^2 and assuming 1% of the area can be used for solar PV panels.
   Correction factor determined by comparing uncorrected area-weighted full-load hours to those
   published in Supplementary Data to Pietzcker, Robert Carl, et al. "Using the sun to decarbonize the power
   sector -- The economic potential of photovoltaics and concentrating solar
   power." Applied Energy 135 (2014): 704-720.
   This correction factor of 0.854337 may be in order if using reanalysis data.
   for discussion refer to this <issue https://github.com/PyPSA/pypsa-eur/issues/285>

``hydro``
---------------

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at:   hydro:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/hydro.csv

.. _lines_cf:

``conventional``
================

Define additional generator attribute for conventional carrier types. If a
scalar value is given it is applied to all generators. However if a string
starting with "data/" is given, the value is interpreted as a path to a csv file
with country specific values. Then, the values are read in and applied to all
generators of the given carrier in the given country. Note that the value(s)
overwrite the existing values.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at:   conventional:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/conventional.csv

``lines``
=============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: lines:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/lines.csv

.. _links_cf:

``links``
=============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: links:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/links.csv

.. _transformers_cf:

``transmission projects``
=======================

Allows to define additional transmission projects that will be added to the base network, e.g., from the TYNDP 2020 dataset. The projects are read in from the CSV files in the subfolder of ``data/transmission_projects/``. New transmission projects, e.g. from TYNDP 2024, can be added in a new subfolder of transmission projects, e.g. ``data/transmission_projects/tyndp2024`` while extending the list of ``transmission_projects`` in the ``config.yaml`` by ``tyndp2024``. The CSV files in the project folder should have the same columns as the CSV files in the template folder ``data/transmission_projects/template``.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: transmission_projects:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/transmission_projects.csv

.. _transformers_cf:

``transformers``
================

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: transformers:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/transformers.csv

.. _load_cf:

``load``
=============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-after: # docs-load
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/load.csv

.. _energy_cf:

``energy``
=======================

.. note::
   Only used for sector-coupling studies.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: energy:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/energy.csv

.. _biomass_cf:

``biomass``
=======================

.. note::
   Only used for sector-coupling studies.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: biomass:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/biomass.csv

The list of available biomass is given by the category in `ENSPRESO_BIOMASS <https://cidportal.jrc.ec.europa.eu/ftp/jrc-opendata/ENSPRESO/ENSPRESO_BIOMASS.xlsx>`__, namely:

- Agricultural waste
- Manure solid, liquid
- Residues from landscape care
- Bioethanol barley, wheat, grain maize, oats, other cereals and rye
- Sugar from sugar beet
- Miscanthus, switchgrass, RCG
- Willow
- Poplar
- Sunflower, soya seed
- Rape seed
- Fuelwood residues
- FuelwoodRW
- C&P_RW
- Secondary Forestry residues - woodchips
- Sawdust
- Municipal waste
- Sludge

.. _solar_thermal_cf:

``solar_thermal``
=======================

.. note::
   Only used for sector-coupling studies.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: solar_thermal:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/solar-thermal.csv

.. _existing_capacities_cf:

``existing_capacities``
=======================

.. note::
   Only used for sector-coupling studies. The value for grouping years are only used in myopic or perfect foresight scenarios.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: existing_capacities:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/existing_capacities.csv

.. _sector_cf:

``sector``
=======================

.. note::
   Only used for sector-coupling studies.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: sector:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/sector.csv

.. _industry_cf:

``industry``
=======================

.. note::
   Only used for sector-coupling studies.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: industry:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/industry.csv

.. _costs_cf:

``costs``
=============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: costs:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/costs.csv


.. _clustering_cf:

``clustering``
==============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: clustering:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/clustering.csv

.. note::
   ``feature:`` in ``simplify_network:``
   are only relevant if ``hac`` were chosen in ``algorithm``.

.. note::
   Build-year aggregation is an experimental feature which significantly reduces the memory-footprint of myopic foresight optimisations. In myopic foresight, there will be one copy of each component for every planning horizon; only the component for the "current" planning horizon will be extendable for each optimisation. By turning on build-year aggregation, these copies are merged into a single component right before the network is solved, and disaggregated again right after the network is solved. This feature should be tested to see if results are acceptable in any given context. The ```exclude_carriers`` should include ``DC``, and you should set the option ``central_heat_everywhere: true`` under the ``sector:`` configuration.

.. tip::
   use ``min`` in ``p_nom_max:`` for more `
   conservative assumptions.

.. _adjustments_cf:

``adjustments``
===============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: adjustments:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/adjustments.csv

.. _solving_cf:

``solving``
=============

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: solving:
   :end-before: # docs

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/solving.csv

.. _plotting_cf:

``plotting``
=============

.. warning::
   More comprehensive documentation for this segment will be released soon.

.. literalinclude:: ../config/config.default.yaml
   :language: yaml
   :start-at: plotting:

.. csv-table::
   :header-rows: 1
   :widths: 22,7,22,33
   :file: configtables/plotting.csv
