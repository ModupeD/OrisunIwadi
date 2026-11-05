#!/usr/bin/env bash
# Create (or reset) an API user on AWS:  infra/scripts/aws-user.sh alice researcher
#
# A random password is stored in Secrets Manager (one secret per user), then the admin task
# reads it from there and saves the user. The password never appears in task settings or logs.
set -euo pipefail

name=${1:?usage: aws-user.sh NAME [viewer|researcher|admin]}
role=${2:-viewer}
case "$role" in viewer|researcher|admin) ;; *) echo "Unknown role: $role" >&2; exit 1 ;; esac
case "$name" in *[!a-zA-Z0-9._-]*) echo "Use letters, digits, . _ - in user names" >&2; exit 1 ;; esac

prefix=$(terraform -chdir=infra/aws output -raw users_secret_prefix)
secret="$prefix$name"

# The password goes through a private temporary file, not the command line.
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
chmod 600 "$tmp"
python3 -c 'import secrets; print(secrets.token_urlsafe(18), end="")' > "$tmp"

if aws secretsmanager describe-secret --secret-id "$secret" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$secret" --secret-string "file://$tmp" >/dev/null
else
  aws secretsmanager create-secret --name "$secret" --description "OncoLake API user $name" \
    --secret-string "file://$tmp" >/dev/null
fi

"$(dirname "$0")/run-task.sh" admin python -m orisun_iwadi.entrypoints.api.users \
  add "$name" --role "$role" --password-secret "$secret"

echo
echo "User $name ($role) is ready. Get the password with:"
echo "  aws secretsmanager get-secret-value --secret-id $secret --query SecretString --output text"
