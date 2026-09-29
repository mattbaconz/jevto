from label import label

assert label(" alpha ") == "unit=ALPHA", "label must normalize names to uppercase"
print("test result: ok")

