from label import label

assert label(" alpha ") == "unit=ALPHA"
assert label("BeTa") == "unit=BETA"
assert label(" 7 ") == "unit=7"

