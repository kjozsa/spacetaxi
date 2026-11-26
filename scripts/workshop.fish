#!/usr/bin/env fish
# the workshop's shortcuts: the run sheet's (docs/agenda.md) long commands, typed once here

function usage
    echo "usage: scripts/workshop.fish <command> [options]"
    echo "  start [OPTIONS]    a fresh race on classic, cavern and canyon loaded, benchmarks given"
    echo "                     (arena.fish up --fresh ...; OPTIONS go to it, e.g. -p 8001)"
    echo "  restart [OPTIONS]  the same without --fresh: the race so far goes on (after a crash)"
    echo "  demo [MAP]         one round of pilot against three greedy as a replay page in the"
    echo "                     browser, no arena or internet needed (MAP classic or canyon,"
    echo "                     default classic)"
end

set root (path resolve (status dirname)/..)
# the maps the arena loads (the first starts) and their benchmarks: seeded, so the same every
# start with the default rules; `up` prints them as `benchmarks on MAP` when it simulates them
set maps -m classic -m cavern -m canyon
set benchmarks -b classic:greedy=428 -b classic:pilot=710 \
    -b cavern:greedy=536 -b cavern:pilot=662 \
    -b canyon:greedy=36 -b canyon:pilot=494

set cmd $argv[1]
set -e argv[1]
switch "$cmd"
    case start
        exec fish $root/scripts/arena.fish up --fresh $maps $benchmarks $argv
    case restart
        exec fish $root/scripts/arena.fish up $maps $benchmarks $argv
    case demo
        set map $argv[1]
        test -n "$map"; or set map classic
        exec fish $root/scripts/sim.fish -m $map -b pilot,greedy,greedy,greedy -n 1
    case '' -h --help
        usage
    case '*'
        echo "workshop: unknown command '$cmd'" >&2
        usage >&2
        exit 2
end
