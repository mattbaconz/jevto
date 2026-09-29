from summarize import total

assert total(["value=2", "warning: cache invalidated", "value=3"]) == 5
assert total(["", "note: harmless", "value=-4", "value=9"]) == 5
assert total(["warning: expected value=99, got 2", "value=2"]) == 2
assert total([" value=7", "value=2"]) == 2, "only lines beginning with value= should count"
assert total([]) == 0
