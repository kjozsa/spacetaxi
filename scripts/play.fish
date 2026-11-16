#!/usr/bin/env fish
# fly a taxi by hand: serves the play page and opens it in the browser (Ctrl-C stops)

function usage
    echo "usage: scripts/play.fish [-m MAP] [-p PORT] [-r KEY=VALUE]... [--no-open]"
    echo "  -m, --map    bundled map name or map file (default cavern)"
    echo "  -p, --port   port to serve on (default 8000)"
    echo "  -r, --rule   override a rule constant, e.g. -r gravity=3 (repeatable)"
    echo "  --no-open    do not open the browser"
end

argparse h/help 'm/map=' 'p/port=' 'r/rule=+' no-open -- $argv
or begin
    usage >&2
    exit 2
end
if set -q _flag_help
    usage
    exit 0
end

set root (path resolve (status dirname)/..)
set -q _flag_map; or set _flag_map cavern
set -q _flag_port; or set _flag_port 8000
set rules
for r in $_flag_rule
    set -a rules --rule $r
end

if not set -q _flag_no_open
    set opener (command -s xdg-open; or command -s open)
    if test -n "$opener"
        # give the server a moment to bind before the browser asks for the page
        fish -c "sleep 1; $opener http://localhost:$_flag_port/" >/dev/null 2>&1 &
    end
end

uv run --project $root spacetaxi play --map $_flag_map --port $_flag_port $rules
