#!/usr/bin/env bash
# Run a one-off task on AWS, wait for it, show its logs, and exit with its exit code.
#
#   infra/scripts/run-task.sh admin               create/update the database users
#   infra/scripts/run-task.sh admin <command...>  run another admin command
#   infra/scripts/run-task.sh etl                 run the daily load now
set -euo pipefail

kind=${1:?usage: run-task.sh admin|etl [command...]}
shift

tf() { terraform -chdir=infra/aws output -json "$1"; }
pick() { python3 -c 'import json,sys; v=json.load(sys.stdin); print(v[sys.argv[1]] if sys.argv[1:] else v)' "$@"; }

cluster=$(tf cluster_name | pick)
family=$(tf task_families | pick "$kind")
group=$(tf security_groups | pick "$kind")
subnets=$(tf private_subnet_ids | pick)
log_group=$(tf log_groups | pick "$kind")

overrides='{}'
if [ $# -gt 0 ]; then
  overrides=$(python3 -c 'import json,sys; print(json.dumps({"containerOverrides": [{"name": sys.argv[1], "command": sys.argv[2:]}]}))' "$kind" "$@")
fi

task=$(aws ecs run-task --cluster "$cluster" --task-definition "$family" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$group],assignPublicIp=DISABLED}" \
  --overrides "$overrides" --query 'tasks[0].taskArn' --output text)
echo "Started $kind task ${task##*/}. Waiting for it to finish..."

while [ "$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task" \
          --query 'tasks[0].lastStatus' --output text)" != "STOPPED" ]; do
  sleep 10
done

aws logs get-log-events --log-group-name "$log_group" \
  --log-stream-name "$kind/$kind/${task##*/}" --start-from-head \
  --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' || true

code=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task" \
  --query 'tasks[0].containers[0].exitCode' --output text)
if [ "$code" != "0" ]; then
  reason=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task" \
    --query 'tasks[0].stoppedReason' --output text)
  echo "Task failed (exit code $code): $reason" >&2
  exit 1
fi
echo "Task finished successfully."
