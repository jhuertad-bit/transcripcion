"""Grant roles access only to our own connection; run manually during provisioning."""
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from counter_presencial.isolation import PREFIX, Resources


def grant(project, token=None, opener=None):
    resources = Resources(project)
    if token is None:
        token = subprocess.run(["gcloud", "auth", "print-access-token", "--project=" + project,
                                "--billing-project=" + project], check=True,
                               capture_output=True, text=True).stdout.strip()
    if not token:
        raise ValueError("Missing authenticated gcloud session")
    opener = opener or urllib.request.urlopen
    endpoint = ("https://bigqueryconnection.googleapis.com/v1/projects/" + project
                + "/locations/us/connections/" + PREFIX + "-gemini")

    def call(action, body):
        request = urllib.request.Request(endpoint + ":" + action, method="POST",
            data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + token,
                                                    "Content-Type": "application/json"})
        with opener(request, timeout=60) as response:
            return json.load(response)

    policy = call("getIamPolicy", {"options": {"requestedPolicyVersion": 3}})
    role, member = "roles/bigquery.connectionUser", "serviceAccount:" + resources.account("pipeline")
    bindings = policy.setdefault("bindings", [])
    binding = next((b for b in bindings if b.get("role") == role and not b.get("condition")), None)
    if binding is None:
        binding = {"role": role, "members": []}
        bindings.append(binding)
    if member in binding.setdefault("members", []):
        return
    binding["members"].append(member)
    # Preserve etag, version, unrelated bindings and conditional bindings.
    call("setIamPolicy", {"policy": policy, "updateMask": "bindings,etag"})


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/grant_connection.py PROJECT_ID")
    grant(sys.argv[1])
    print("Dedicated connection permission configured.")
