import boto3
import os
import time
import re
from datetime import datetime
from botocore.exceptions import ClientError

rds = boto3.client('rds')

def wait_for_snapshot_available(snapshot_id):
    print(f"Waiting for snapshot {snapshot_id} to become available...")
    waiter = rds.get_waiter('db_snapshot_available')
    waiter.wait(DBSnapshotIdentifier=snapshot_id)
    print(f"Snapshot {snapshot_id} is now available.")

def snapshot_exists(snapshot_id):
    try:
        rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'DBSnapshotNotFound':
            return False
        else:
            raise

def snapshot_is_available(snapshot_id):
    resp = rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
    status = resp['DBSnapshots'][0]['Status']
    return status == 'available'

def db_instance_exists(db_id):
    try:
        rds.describe_db_instances(DBInstanceIdentifier=db_id)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'DBInstanceNotFound':
            return False
        else:
            raise

def db_instance_is_available(db_id):
    resp = rds.describe_db_instances(DBInstanceIdentifier=db_id)
    status = resp['DBInstances'][0]['DBInstanceStatus']
    return status == 'available'

def handler(event, context):
    print("Event received:", event)
    source_db_instance_identifier = os.environ['SOURCE_DB']
    snapshot_identifier = os.environ.get('SNAPSHOT_ID', None)
    kms_key_arn = os.environ.get('KMS_KEY_ARN')

    try:
        if not snapshot_identifier:
            raise ValueError("SNAPSHOT_ID environment variable is not set or empty")
        if not kms_key_arn:
            raise ValueError("KMS_KEY_ARN environment variable is not set or empty")

        # Step 1: Copy snapshot if needed (optional, if you want to copy with KMS)
        copied_snapshot_id = sanitize_snapshot_id(snapshot_identifier)
        if snapshot_exists(copied_snapshot_id):
            if not snapshot_is_available(copied_snapshot_id):
                wait_for_snapshot_available(copied_snapshot_id)
            else:
                print(f"Snapshot {copied_snapshot_id} already available.")
        else:
            print(f"Copying snapshot {snapshot_identifier} to {copied_snapshot_id} using KMS key {kms_key_arn}...")
            rds.copy_db_snapshot(
                SourceDBSnapshotIdentifier=snapshot_identifier,
                TargetDBSnapshotIdentifier=copied_snapshot_id,
                KmsKeyId=kms_key_arn
            )
            wait_for_snapshot_available(copied_snapshot_id)

        # Step 2: Restore the snapshot to a new instance
        restored_db_identifier = f"{source_db_instance_identifier}-restored"
        if db_instance_exists(restored_db_identifier):
            if not db_instance_is_available(restored_db_identifier):
                print(f"Waiting for DB instance {restored_db_identifier} to become available...")
                waiter = rds.get_waiter('db_instance_available')
                waiter.wait(DBInstanceIdentifier=restored_db_identifier)
            else:
                print(f"Restored DB instance {restored_db_identifier} already available.")
        else:
            db_info = rds.describe_db_instances(DBInstanceIdentifier=source_db_instance_identifier)
            db_instance = db_info['DBInstances'][0]
            option_group_name = db_instance['OptionGroupMemberships'][0]['OptionGroupName']
            subnet_group_name = db_instance['DBSubnetGroup']['DBSubnetGroupName']
            print(f"Restoring DB from copied snapshot: {copied_snapshot_id}")
            rds.restore_db_instance_from_db_snapshot(
                DBInstanceIdentifier=restored_db_identifier,
                DBSnapshotIdentifier=copied_snapshot_id,
                DBInstanceClass=db_instance['DBInstanceClass'],
                PubliclyAccessible=db_instance['PubliclyAccessible'],
                OptionGroupName=option_group_name,
                DBSubnetGroupName=subnet_group_name
            )
            print("Restore initiated. Waiting for instance to be available...")
            waiter = rds.get_waiter('db_instance_available')
            waiter.wait(DBInstanceIdentifier=restored_db_identifier)

        # Step 3: Delete the original instance
        if db_instance_exists(source_db_instance_identifier):
            print(f"Deleting original instance: {source_db_instance_identifier}")
            try:
                # Disable deletion protection if enabled
                db_instance = rds.describe_db_instances(DBInstanceIdentifier=source_db_instance_identifier)['DBInstances'][0]
                if db_instance.get('DeletionProtection', False):
                    print(f"Disabling deletion protection for {source_db_instance_identifier}")
                    rds.modify_db_instance(
                        DBInstanceIdentifier=source_db_instance_identifier,
                        DeletionProtection=False,
                        ApplyImmediately=True
                    )
                    # Wait for the modification to take effect
                    time.sleep(10)
                rds.delete_db_instance(DBInstanceIdentifier=source_db_instance_identifier, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
                print(f"Delete initiated for {source_db_instance_identifier}")
                # Wait for deletion to complete
                waiter = rds.get_waiter('db_instance_deleted')
                waiter.wait(DBInstanceIdentifier=source_db_instance_identifier)
            except ClientError as e:
                if e.response['Error']['Code'] == 'DBInstanceNotFound':
                    print(f"Original instance {source_db_instance_identifier} not found, may have already been deleted.")
                else:
                    raise
        else:
            print(f"Original instance {source_db_instance_identifier} does not exist or already deleted.")

        # Step 4: Rename the restored instance to the original name
        print(f"Renaming {restored_db_identifier} to {source_db_instance_identifier}")
        rds.modify_db_instance(
            DBInstanceIdentifier=restored_db_identifier,
            NewDBInstanceIdentifier=source_db_instance_identifier,
            ApplyImmediately=True
        )
        # Wait for rename to complete
        waiter = rds.get_waiter('db_instance_available')
        waiter.wait(DBInstanceIdentifier=source_db_instance_identifier)

        print("Restore, delete, and rename complete. Only the original name should remain.")
        return {
            'statusCode': 200,
            'body': f"Restored snapshot to new instance, deleted original, and renamed restored to {source_db_instance_identifier}."
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': str(e)
        }

def sanitize_snapshot_id(original_id):
    """
    Ensures the snapshot identifier is valid per AWS rules:
    - Start with a letter
    - Use only lowercase letters, digits, hyphens
    - No consecutive hyphens or trailing hyphen
    - Deterministic: no timestamp, so the same source snapshot always maps to the same copy name
    """
    # Extract the last part if ARN is provided
    original_id = original_id.split(":")[-1]
    # Remove invalid characters
    sanitized = re.sub(r'[^a-z0-9\-]', '', original_id.lower())
    sanitized = re.sub(r'-+', '-', sanitized).strip('-')
    # Ensure starts with a letter
    if not sanitized[0].isalpha():
        sanitized = 'a' + sanitized
    return f"{sanitized}-copy"
