#!/usr/bin/env fish
# run the workshop arena: start it, add and remove house bots, switch maps, see what is going on
# state (pid files, logs, the control pipe, standings, log, replays) lives in out/arena/PORT/

function usage
    echo "usage: scripts/arena.fish <command> [-p PORT] [options]"
    echo "  up [-m MAP]... [--fresh] [-r KEY=VALUE]... [-f SECONDS]"
    echo "                     serve the arena in the foreground (Ctrl-C stops)"
    echo "  house [pilot|greedy] [-n N]"
    echo "                     start N house bots of that kind (default one pilot, one greedy)"
    echo "  unhouse [NAME|all] stop house bots started by house (default all)"
    echo "  map NAME           switch the next wave's map"
    echo "  maps               list the maps (the answer is in the arena's output)"
    echo "  kick NAME          disconnect a bot"
    echo "  ban NAME           disconnect a bot, refuse its name and drop it from the standings"
    echo "  unban NAME         accept a banned name again"
    echo "  lesson [N]         open lessons 1 to N on every /lessons page (0 hides; no N: how many)"
    echo "  status             is it up, the map, who plays, the house bots, the top 5"
    echo "  open               open /watch and /standings in the browser"
    echo "  down               stop the house bots and the arena started by up"
    echo "options:"
    echo "  -p, --port    the arena's port (default 8000)"
    echo "  -m, --map     bundled map name or map file, repeatable (default cavern)"
    echo "  --fresh       archive the race so far (standings, log, replays) and start a new one"
    echo "  -r, --rule    override a rule constant, e.g. -r round_ticks=300 (repeatable)"
    echo "  -f, --fill    seconds from a bot joining or a wave ending to the next wave (default 10)"
    echo "  -n, --number  how many house bots of each kind (default 1)"
    echo "house bots are named ace, ace2, ace3 (pilot) and gus, gus2, gus3 (greedy)"
end

function fail
    echo "arena: $argv" >&2
    exit 1
end

set root (path resolve (status dirname)/..)
set spare_pilot ace ace2 ace3
set spare_greedy gus gus2 gus3

set cmd $argv[1]
set -e argv[1]
if test -z "$cmd"; or contains -- $cmd -h --help
    usage
    exit 0
end
if not contains -- $cmd up house unhouse map maps kick ban unban lesson status open down
    echo "arena: unknown command '$cmd'" >&2
    usage >&2
    exit 2
end

argparse h/help 'p/port=' 'm/map=+' fresh 'r/rule=+' 'f/fill=' 'n/number=' -- $argv
or begin
    usage >&2
    exit 2
end
if set -q _flag_help
    usage
    exit 0
end
set -q _flag_port; or set _flag_port 8000
set port $_flag_port
set dir $root/out/arena/$port
set pipe $dir/control

function py
    uv run --project $root --quiet python -c $argv
end

# whether something answers on the port as an arena
function answers
    py 'import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/leaderboard.json", timeout=2)
except Exception:
    sys.exit(1)' $port
end

# whether the process of a pid file runs
function alive --argument-names pidfile
    test -f $pidfile; and kill -0 (cat $pidfile) 2>/dev/null
end

# stop the process of a pid file (that PID only) and wait for it to exit
function stop --argument-names pidfile
    if alive $pidfile
        kill (cat $pidfile)
        for i in (seq 50)
            alive $pidfile; or break
            sleep 0.1
        end
    end
    rm -f $pidfile
end

function house_names
    for f in $dir/house-*.pid
        string replace -r '.*/house-(.*)\.pid$' '$1' $f
    end
end

switch $cmd
    case up
        answers; and fail "an arena already answers on port $port; stop it first (down)"
        mkdir -p $dir
        if set -q _flag_fresh
            set race
            for f in standings.json log.jsonl replays
                test -e $dir/$f; and set -a race $dir/$f
            end
            if set -q race[1]
                set archive $dir/archive-(date +%Y%m%d-%H%M%S)
                mkdir -p $archive; and mv $race $archive/
                or fail "cannot archive the race in $archive"
                echo "the race so far is in $archive"
            end
        end
        set -q _flag_map; or set _flag_map cavern
        set extra
        for m in $_flag_map
            set -a extra --map $m
        end
        for r in $_flag_rule
            set -a extra --rule $r
        end
        set -q _flag_fill; and set -a extra --fill-seconds $_flag_fill
        for name in $spare_pilot $spare_greedy
            set -a extra --house $name
        end
        echo $fish_pid >$dir/serve.pid
        # exec keeps this PID, so serve.pid names the arena (uv passes a kill on to serve)
        exec uv run --project $root spacetaxi serve --port $port $extra \
            --standings $dir/standings.json --log $dir/log.jsonl --replays $dir/replays \
            --control $pipe

    case house
        set kinds $argv[1]
        test -n "$kinds"; or set kinds pilot greedy
        for kind in $kinds
            contains -- $kind pilot greedy; or fail "house: no kind '$kind'; kinds: pilot, greedy"
        end
        set -q _flag_number; or set _flag_number 1
        string match -qr '^[1-9][0-9]*$' -- $_flag_number; or fail "house: -n must be a positive number"
        answers; or fail "no arena answers on port $port (start it with up)"
        mkdir -p $dir
        set started
        for kind in $kinds
            set free
            set spare spare_$kind
            for name in $$spare
                alive $dir/house-$name.pid; or set -a free $name
            end
            if test (count $free) -lt $_flag_number
                fail "house: the spare $kind names are used up ("(count $free)" free; unhouse some first)"
            end
            for name in $free[1..$_flag_number]
                uv run --project $root spacetaxi connect --bot $kind --name $name \
                    --server ws://127.0.0.1:$port/bot </dev/null >$dir/house-$name.log 2>&1 &
                echo $last_pid >$dir/house-$name.pid
                disown $last_pid
                set -a started "$name ($kind)"
            end
        end
        echo "started: "(string join ', ' $started)

    case unhouse
        set which $argv[1]
        test -n "$which"; or set which all
        if test $which = all
            set names (house_names)
        else
            set names (string lower -- $which)
            test -f $dir/house-$names.pid; or fail "unhouse: no house bot '$which' started here"
        end
        set stopped
        for name in $names
            alive $dir/house-$name.pid; and set -a stopped $name
            stop $dir/house-$name.pid
        end
        if set -q stopped[1]
            echo "stopped: "(string join ', ' $stopped)
        else
            echo "no house bots running"
        end

    case map maps kick ban unban lesson
        if test $cmd = maps; or begin; test $cmd = lesson; and not set -q argv[1]; end
            set line $cmd
        else
            set -q argv[1]; or fail "$cmd: which one? ($cmd NAME)"
            set line "$cmd $argv[1]"
        end
        if not alive $dir/serve.pid; or not test -p $pipe
            fail "the arena on port $port is not running (start it with up)"
        end
        echo $line >$pipe
        echo "sent '$line'; the answer is in the arena's output"

    case status
        set running
        for name in (house_names)
            alive $dir/house-$name.pid; and set -a running $name
        end
        py 'import json, sys, urllib.request
port, house = sys.argv[1], sys.argv[2:]
def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
        return json.load(r)
try:
    board, standings = get("/leaderboard.json"), get("/standings.json")
except Exception:
    sys.exit(f"arena :{port} is not answering")
playing = sum(len(a["players"]) for a in board["arenas"])
now, then, wave, waiting = (standings["map_name"], board["next_map"], board["wave"],
                            board["waiting"])
print(f"arena :{port} up  map {now}, next {then}  wave {wave}  "
      f"{waiting} waiting, {playing} playing")
print("house: " + (" ".join(house) if house else "none"))
rows = standings["standings"][:5]
for row in rows:
    rank, name, points, got = row["rank"], row["name"], row["points"], len(row["milestones"])
    print(f"{rank:>3}. {name:<12} {points:>5} pts  {got} milestones")
if not rows:
    print("no standings yet")' $port $running

    case open
        answers; or fail "no arena answers on port $port (start it with up)"
        set opener (command -s xdg-open; or command -s open)
        test -n "$opener"; or fail "open: no xdg-open or open to start the browser with"
        set pages watch standings
        for page in $pages
            $opener http://localhost:$port/$page >/dev/null 2>&1 &
            disown $last_pid
        end

    case down
        set names (house_names)
        for name in $names
            stop $dir/house-$name.pid
        end
        set -q names[1]; and echo "stopped house bots: "(string join ', ' $names)
        # serve removes the pipe on exit: without it serve.pid is stale (Ctrl-C), its PID maybe reused
        if alive $dir/serve.pid; and test -p $pipe
            stop $dir/serve.pid
            echo "stopped the arena on port $port"
        else
            rm -f $dir/serve.pid
            echo "no arena started by up runs on port $port"
        end
end
