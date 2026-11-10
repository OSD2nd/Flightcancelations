"""Synthetic check: if fog in the record always meant cancellation, the trained weight for fog should rise."""
import random, sys
sys.path.insert(0, ".")
from ksalerts import features as F, history

random.seed(1)
X, y = [], []
for _ in range(400):
    x = {k: 0 for k in F.FEATURES}
    x["fog"] = int(random.random() < 0.15)
    x["gust_35"] = int(random.random() < 0.2)
    p = 0.7 if x["fog"] else 0.02          # truth: fog is brutal, wind is harmless
    X.append(x); y.append(int(random.random() < p))
w = history.fit(X, y)
print("fog      prior %.2f -> %.2f" % (F.PRIOR["fog"], w["fog"]))
print("gust_35  prior %.2f -> %.2f" % (F.PRIOR["gust_35"], w["gust_35"]))
print("bias     prior %.2f -> %.2f" % (F.PRIOR["bias"], w["bias"]))
assert w["fog"] > F.PRIOR["fog"] + 0.5
assert w["gust_35"] < F.PRIOR["gust_35"]
# tiny record should barely move things
w2 = history.fit(X[:10], y[:10])
assert abs(w2["fog"] - F.PRIOR["fog"]) < abs(w["fog"] - F.PRIOR["fog"])
print("ok")
