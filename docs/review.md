# Code Review Notes

The two issues raised in the previous review have now been addressed:

* `_ecc_multiscale` in `app/services/compare_service.py` now accepts the ECC convergence settings explicitly, avoiding the `th` scope leak that previously resulted in a `NameError` if the helper were called.
* `ToolService.load_recipe()` guards against all-zero golden images before rescaling, preventing a divide-by-zero when converting to `uint8`.
