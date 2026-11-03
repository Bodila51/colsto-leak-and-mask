"""Seal or reveal the blind-test truth.

    python recorder/seal_truth.py seal     # writes truth/SEALED.sha256 (commit this BEFORE any answer)
    python recorder/seal_truth.py reveal   # copies data/sealed/truth/*.json to truth/ and checks the hashes
"""
import hashlib, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC, DST = os.path.join(ROOT, "data", "sealed", "truth"), os.path.join(ROOT, "truth")
SEAL = os.path.join(DST, "SEALED.sha256")


def digest(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


if sys.argv[1] == "seal":
    os.makedirs(DST, exist_ok=True)
    with open(SEAL, "w", newline="\n") as f:
        for name in sorted(os.listdir(SRC)):
            f.write(f"{digest(os.path.join(SRC, name))}  {name}\n")
    print(open(SEAL).read())
else:
    for line in open(SEAL):
        h, name = line.split()
        shutil.copyfile(os.path.join(SRC, name), os.path.join(DST, name))
        print(name, "OK" if digest(os.path.join(DST, name)) == h else "HASH MISMATCH")
