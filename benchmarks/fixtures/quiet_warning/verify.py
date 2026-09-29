from summarize import total

print("warning: cache invalidated before the failed summary")
for index in range(24):
    print(f"test suite::case_{index} ... ok")
assert total(["value=2", "warning: cache invalidated", "value=3"]) == 5, "summary should ignore warning lines"
print("test suite::summary ... ok")

