# JevTO terminal demo. Render with VHS (https://github.com/charmbracelet/vhs):
#   vhs assets/demo/demo.tape
# Needs: a built jevto on PATH and the rust-test-failure workspace from
# benchmarks/token_bench.py at $env:JEVTO_DEMO_WS (see assets/demo/README.md).

Output assets/demo/jevto-demo.gif

Set Shell "powershell"
Set FontFamily "Cascadia Mono"
Set FontSize 17
Set Width 1280
Set Height 760
Set Padding 30
Set LineHeight 1.25
Set TypingSpeed 38ms
Set CursorBlink false
Set Theme { "name": "jevto", "background": "#111214", "foreground": "#E9E6DF", "cursor": "#E5231B", "selection": "#34373D", "black": "#111214", "red": "#E5231B", "green": "#7BB662", "yellow": "#E0B04A", "blue": "#6F9BD1", "magenta": "#C48BC4", "cyan": "#6FB7B7", "white": "#E9E6DF", "brightBlack": "#8B8F97", "brightRed": "#FF4B42", "brightGreen": "#98D17E", "brightYellow": "#F2C66B", "brightBlue": "#8FB4E3", "brightMagenta": "#D9A6D9", "brightCyan": "#8FD1D1", "brightWhite": "#F7F5F0" }

Hide
Type "function prompt { '$ ' }; cd $env:JEVTO_DEMO_WS; cls"
Enter
Sleep 1.5s
Show

Type "# An agent runs the test suite. This is what lands in its context:"
Sleep 600ms
Enter
Type "cargo test"
Sleep 400ms
Enter
Sleep 2.5s
Type "# 173 lines and ~1,250 tokens, for one failing assertion."
Sleep 2.5s
Enter

Hide
Type "cls"
Enter
Show

Type "# Same command through JevTO: the failure, the assertion, and a recall handle."
Sleep 600ms
Enter
Type "jevto run -- cargo test"
Sleep 400ms
Enter
Sleep 5.5s

Type "# Nothing is lost. Any hidden gap comes back byte-for-byte:"
Sleep 600ms
Enter
Type "jevto recall last --section stdout-19-4219 | select -first 3"
Sleep 400ms
Enter
Sleep 5s
