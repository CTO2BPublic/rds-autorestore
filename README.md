# RDS AutoRestore

A utility to automate the process of restoring an AWS RDS instance from a snapshot, replacing the original instance. This script is designed to be used as an AWS Lambda function or as a standalone script for disaster recovery or automated restore workflows.

## Features
- Restores the snapshot to a new DB instance
- Deletes the original DB instance (with deletion protection handling)
- Renames the restored instance to the original name
- Waits for all operations to complete before proceeding

## Prerequisites
- Python 3.7+
- AWS credentials with permissions for RDS and KMS operations
- boto3 and botocore Python packages

## Installation

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd rds-autorestore
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Set the following environment variables before running the script:

- `SOURCE_DB`: The DB instance identifier of the source RDS instance to be restored.
- `SNAPSHOT_ID`: The identifier (or ARN) of the RDS snapshot to restore from.

Example:
```bash
export SOURCE_DB=mydb-instance
export SNAPSHOT_ID=arn:aws:rds:us-east-1:123456789012:snapshot:rds:mydb-snapshot
```

## Usage

You can run the script as a Lambda function (using the `handler`) or as a standalone script (by adapting the code to call `handler` with appropriate arguments).

### As a Lambda Function
- Deploy `app.py` to AWS Lambda.
- Set the required environment variables in the Lambda configuration.
- Trigger the function with an event (the event content is not used).

### As a Standalone Script
- Set the required environment variables in your shell.
- Modify the script to call `lambda_handler({}, None)` at the end of the file, or adapt as needed.

## What the Script Does
1. **Copies the snapshot** (if not already copied with the KMS key).
2. **Restores** the snapshot to a new DB instance (`<original>-restored`).
3. **Deletes** the original DB instance (disabling deletion protection if necessary).
4. **Renames** the restored instance to the original name.
5. **Waits** for each operation to complete before proceeding.

## Notes
- The script assumes the source DB instance and snapshot exist and are accessible.
- The script disables deletion protection if enabled on the original instance before deletion.
- The script skips final snapshot creation when deleting the original instance.
- The script waits for each AWS operation to complete before moving to the next step.
- The script is designed to be idempotent and will not repeat steps if resources already exist in the desired state.

## License

See [LICENSE](LICENSE) for details.
