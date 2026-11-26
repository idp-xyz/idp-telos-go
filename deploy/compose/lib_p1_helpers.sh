# What PATH P1 and PATH P2 read from the CMI's metrics (deploy/compose/path_p1.sh and path_p2.sh `source` this; the caller supplies p1_metrics, the CMI's Prometheus text).
set -u
# p1_metric NAME [label=value ...]: the sum of the samples of NAME that carry all the labels (0 when there are none); the caller's p1_metrics prints the text of every CMI replica (kind runs two), so a
# state counts as reached when at least one replica reports it, and a counter or a last-success time has grown when the sum has
p1_metric() { p1_metrics | python3 -c "
import re,sys
name,want=sys.argv[1],sys.argv[2:]; tot=0.0
for l in sys.stdin:
    if not l.startswith(name+'{') and not l.startswith(name+' '): continue
    if all(('%s=\"%s\"' % tuple(w.split('=',1))) in l for w in want): tot+=float(l.rsplit(' ',1)[1])
print(int(tot) if tot==int(tot) else tot)" "$@"; }
# p1_metric_max NAME [label=value ...]: the largest of those samples (a last-success time: the replica that embedded last has the newest)
p1_metric_max() { p1_metrics | python3 -c '
import sys
name,want=sys.argv[1],[w.replace("=", "=\"", 1) + "\"" for w in sys.argv[2:]]; best=0.0
for l in sys.stdin:
    if not l.startswith(name + "{") and not l.startswith(name + " "): continue
    if all(w in l for w in want): best=max(best, float(l.rsplit(" ", 1)[1]))
print(best)' "$@"; }
