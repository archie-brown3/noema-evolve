#!/usr/bin/env bash
# usage: log-cost.sh <stage> <usd>  — appends one row to the usage log.
echo -e "$(date -Is)\t$1\t$2" >> "$(dirname "$0")/../memory/usage.log"
