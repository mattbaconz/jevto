# Actual released CLI, on the generated fixture prepared by prepare_launch.py.
# VHS records a terminal; no product UI or command output is fabricated.
Output site/demo/jevto-demo.mp4
Set Shell "powershell"
Set FontFamily "Cascadia Mono"
Set FontSize 22
Set Width 1280
Set Height 760
Set Padding 30
Set LineHeight 1.2
Set TypingSpeed 16ms
Set CursorBlink false
Set Theme { "name": "jevto", "background": "#111214", "foreground": "#E9E6DF", "cursor": "#E5231B", "selection": "#34373D", "black": "#111214", "red": "#E5231B", "green": "#7BB662", "yellow": "#E0B04A", "blue": "#6F9BD1", "magenta": "#C48BC4", "cyan": "#6FB7B7", "white": "#E9E6DF", "brightBlack": "#8B8F97", "brightRed": "#FF4B42", "brightGreen": "#98D17E", "brightYellow": "#F2C66B", "brightBlue": "#8FB4E3", "brightMagenta": "#D9A6D9", "brightCyan": "#8FD1D1", "brightWhite": "#F7F5F0" }
Hide
Type "function prompt { '$ ' }; cd $env:JEVTO_DEMO_WS; cls"
Enter
Sleep 1s
Show
Type "jevto --version"
Enter
Sleep 1s
Type "# One failure among 150 passing tests."
Enter
Type "cargo test"
Enter
Sleep 3s
Hide
Type "cls"
Enter
Show
Type "# Same command. Rules only. No API key."
Enter
Type "jevto run --mode rules -- cargo test"
Enter
Sleep 6s
Hide
Type "cls"
Enter
Show
Type "# Recall the original stdout. First four lines shown:"
Enter
Type "jevto recall last --lines 1:4"
Enter
Sleep 4s
Type "# Less tool output. Exact recall.  jevto.xyz"
Enter
Sleep 3s
