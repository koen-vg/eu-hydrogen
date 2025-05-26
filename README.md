[![DOI](https://zenodo.org/badge/900660772.svg)](https://doi.org/10.5281/zenodo.15316483)


# Little to lose: the case for a robust European green hydrogen strategy

This repository contains the necessary code to reproduce the results in the paper "Little to lose: the case for a robust European green hydrogen strategy" ([preprint](https://arxiv.org/abs/2412.07464)). The repository is based directly on [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur). Modifications can be inspected here in the git history, and are also documented in the paper.

The main results are based on the `config/eu-hydrogen.yaml` configuration. In order to reproduce the results, follow the PyPSA-Eur installation instructions and run:
```bash
$ snakemake --configfile config/eu-hydrogen.yaml -call all_near_opt_myopic
```
With 72 total scenarios, 6 planning horizons, cost-optimisations as well as hydrogen min- and maximisations at three slack levels (making for 1 + 3*2 = 7 optimisations per scenario and planning horizon), this will run a total of 3024 optimisations.
Finally, using the `config/ccs-scenarios.yaml` configuration to reproduce the CCS-specific sensitivity analysis.

The `notebooks` directory contains jupyter notebooks with the all code used to generate the figures in the article. All statistics needed for this figures have been compiled from the solved models and stored under `notebooks/cache_results.csv`. The main plotting notebook (`paper_plots.ipynb`), automatically uses these statistics, so that the plots can be reproduced without re-running all optimisations.

The solved networks can be found at [https://doi.org/10.11582/2025.00068](https://doi.org/10.11582/2025.00068). These can be used to further investigate results, beyond the information used in the paper (and provided in aggregate in the cached result files).

All modifications to PyPSA-Eur and plotting code is licensed under the MIT license. The figures themselves are licensed under CC-BY-4.0.
