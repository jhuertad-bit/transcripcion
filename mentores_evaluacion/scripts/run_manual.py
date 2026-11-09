"""Inicia el Workflow con impersonacion explicita; no modifica la configuracion gcloud."""
import argparse
from datetime import date
import json
import subprocess

SERVICE_ACCOUNT = "genesys-audio-processor@prd-utpbi-data-operation.iam.gserviceaccount.com"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--reprocess", action="store_true")
    args = parser.parse_args()
    payload = {}
    if args.date_from or args.date_to or args.reprocess:
        if not (args.date_from and args.date_to and args.reprocess):
            parser.error("Un reproceso requiere --date-from, --date-to y --reprocess")
        if date.fromisoformat(args.date_from) > date.fromisoformat(args.date_to):
            parser.error("Rango invertido")
        payload = dict(date_from=args.date_from, date_to=args.date_to, reprocess=True)
    subprocess.run(["gcloud.cmd" if __import__("os").name == "nt" else "gcloud",
                    "workflows", "execute", "prd-utpbi-mentores-evaluacion-pipeline",
                    "--project=prd-utpbi-data-operation", "--location=us-central1",
                    "--impersonate-service-account=" + SERVICE_ACCOUNT,
                    "--data=" + json.dumps(payload)], check=True)


if __name__ == "__main__":
    main()
