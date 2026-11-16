#!/usr/bin/env fish
# run a headless simulation, print the results and watch the replay in the browser

function usage
    echo "usage: scripts/sim.fish [-m MAP] [-b BOTS] [-n ROUNDS] [-s SEED] [-r KEY=VALUE]... [-p PORT] [--no-open]"
    echo "  -m, --map     bundled map name or map file (default cavern)"
    echo "  -b, --bots    comma-separated bots: idle, random, greedy, pilot or .py paths (default pilot,greedy)"
    echo "  -n, --rounds  rounds to play (default 3)"
    echo "  -s, --seed    random seed (default 1)"
    echo "  -r, --rule    override a rule constant, e.g. -r gravity=3 (repeatable)"
    echo "  -p, --port    serve the replay on this port instead of writing an HTML file"
    echo "  --no-open     do not open the browser"
    echo "replays and pages go to out/ in the repo"
end

argparse h/help 'm/map=' 'b/bots=' 'n/rounds=' 's/seed=' 'r/rule=+' 'p/port=' no-open -- $argv
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
set -q _flag_bots; or set _flag_bots pilot,greedy
set -q _flag_rounds; or set _flag_rounds 3
set -q _flag_seed; or set _flag_seed 1
set rules
for r in $_flag_rule
    set -a rules --rule $r
end

mkdir -p $root/out
set name (path change-extension '' (path basename $_flag_map))-s$_flag_seed
set replay $root/out/$name.jsonl

uv run --project $root spacetaxi run --map $_flag_map --bots $_flag_bots \
    --rounds $_flag_rounds --seed $_flag_seed --replay $replay $rules
or exit

set opener (command -s xdg-open; or command -s open)
if set -q _flag_port
    if not set -q _flag_no_open; and test -n "$opener"
        fish -c "sleep 1; $opener http://localhost:$_flag_port/" >/dev/null 2>&1 &
    end
    uv run --project $root spacetaxi view $replay --port $_flag_port
else
    set html $root/out/$name.html
    uv run --project $root spacetaxi view $replay --out $html
    or exit
    if not set -q _flag_no_open; and test -n "$opener"
        $opener $html >/dev/null 2>&1 &
    end
end
