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
    restored_db_identifier = f"{source_db_instance_identifier}-restored"

    try:
        # Check existence of both instances
        original_exists = db_instance_exists(source_db_instance_identifier)
        restored_exists = db_instance_exists(restored_db_identifier)
        restored_available = restored_exists and db_instance_is_available(restored_db_identifier)

        print(f"Original instance {source_db_instance_identifier} exists: {original_exists}")
        print(f"Restored instance {restored_db_identifier} exists: {restored_exists}")
        print(f"Restored instance {restored_db_identifier} is available: {restored_available}")

        # If original is missing, restored exists and is available, finish rename
        if not original_exists and restored_available:
            print(f"Original instance {source_db_instance_identifier} missing, but {restored_db_identifier} is available. Proceeding to rename.")
            rds.modify_db_instance(
                DBInstanceIdentifier=restored_db_identifier,
                NewDBInstanceIdentifier=source_db_instance_identifier,
                ApplyImmediately=True
            )
            waiter = rds.get_waiter('db_instance_available')
            waiter.wait(DBInstanceIdentifier=source_db_instance_identifier)
            print("Rename complete.")
            return {
                'statusCode': 200,
                'body': f"Renamed {restored_db_identifier} to {source_db_instance_identifier}."
            }

        # If both missing, nothing to do
        if not original_exists and not restored_exists:
            print(f"Neither {source_db_instance_identifier} nor {restored_db_identifier} exist. Nothing to do.")
            return {
                'statusCode': 404,
                'body': f"Neither {source_db_instance_identifier} nor {restored_db_identifier} exist."
            }

        # If restored does not exist, proceed with restore from snapshot
        if not restored_exists:
            print(f"Restored instance {restored_db_identifier} does not exist. Proceeding with restore from snapshot.")
            if not snapshot_identifier:
                raise ValueError("SNAPSHOT_ID environment variable is not set or empty")
            if not snapshot_exists(snapshot_identifier):
                raise ValueError(f"Snapshot {snapshot_identifier} does not exist.")
            if not snapshot_is_available(snapshot_identifier):
                wait_for_snapshot_available(snapshot_identifier)
            db_info = rds.describe_db_instances(DBInstanceIdentifier=source_db_instance_identifier)
            db_instance = db_info['DBInstances'][0]
            option_group_name = db_instance['OptionGroupMemberships'][0]['OptionGroupName']
            subnet_group_name = db_instance['DBSubnetGroup']['DBSubnetGroupName']
            parameter_group_name = db_instance['DBParameterGroups'][0]['DBParameterGroupName']
            source_arn = db_instance['DBInstanceArn']
            tags_response = rds.list_tags_for_resource(ResourceName=source_arn)
            tags = tags_response.get('TagList', [])
            vpc_security_group_ids = [sg['VpcSecurityGroupId'] for sg in db_instance.get('VpcSecurityGroups', [])]
            print(f"Restoring DB from snapshot: {snapshot_identifier}")
            rds.restore_db_instance_from_db_snapshot(
                DBInstanceIdentifier=restored_db_identifier,
                DBSnapshotIdentifier=snapshot_identifier,
                DBInstanceClass=db_instance['DBInstanceClass'],
                PubliclyAccessible=db_instance['PubliclyAccessible'],
                OptionGroupName=option_group_name,
                DBSubnetGroupName=subnet_group_name,
                DBParameterGroupName=parameter_group_name,
                DeletionProtection=True,
                Tags=tags,
                VpcSecurityGroupIds=vpc_security_group_ids
            )
            print("Restore initiated. Waiting for instance to be available...")
            waiter = rds.get_waiter('db_instance_available')
            waiter.wait(DBInstanceIdentifier=restored_db_identifier)
            # After restore, continue to delete and rename

        # Step 3: Delete the original instance if it exists
        if original_exists:
            print(f"Deleting original instance: {source_db_instance_identifier}")
            try:
                db_instance = rds.describe_db_instances(DBInstanceIdentifier=source_db_instance_identifier)['DBInstances'][0]
                if db_instance.get('DeletionProtection', False):
                    print(f"Disabling deletion protection for {source_db_instance_identifier}")
                    rds.modify_db_instance(
                        DBInstanceIdentifier=source_db_instance_identifier,
                        DeletionProtection=False,
                        ApplyImmediately=True
                    )
                    time.sleep(10)
                rds.delete_db_instance(DBInstanceIdentifier=source_db_instance_identifier, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
                print(f"Delete initiated for {source_db_instance_identifier}")
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
        if db_instance_is_available(restored_db_identifier):
            print(f"Renaming {restored_db_identifier} to {source_db_instance_identifier}")
            rds.modify_db_instance(
                DBInstanceIdentifier=restored_db_identifier,
                NewDBInstanceIdentifier=source_db_instance_identifier,
                ApplyImmediately=True
            )
            # Robustly wait for the new instance to appear and become available
            import time
            max_wait = 300  # seconds
            poll_interval = 10
            waited = 0
            while waited < max_wait:
                try:
                    resp = rds.describe_db_instances(DBInstanceIdentifier=source_db_instance_identifier)
                    status = resp['DBInstances'][0]['DBInstanceStatus']
                    print(f"Instance {source_db_instance_identifier} status after rename: {status}")
                    if status == 'available':
                        break
                except ClientError as e:
                    if e.response['Error']['Code'] == 'DBInstanceNotFound':
                        print(f"Instance {source_db_instance_identifier} not found yet after rename. Waiting...")
                    else:
                        raise
                time.sleep(poll_interval)
                waited += poll_interval
            else:
                print(f"Timeout waiting for instance {source_db_instance_identifier} to appear after rename.")
                return {
                    'statusCode': 202,
                    'body': f"Timeout waiting for instance {source_db_instance_identifier} to appear after rename. Please retry later."
                }
            # Now wait for full availability
            waiter = rds.get_waiter('db_instance_available')
            waiter.wait(DBInstanceIdentifier=source_db_instance_identifier)
            print("Restore, delete, and rename complete. Only the original name should remain.")
            return {
                'statusCode': 200,
                'body': f"Restored snapshot to new instance, deleted original, and renamed restored to {source_db_instance_identifier}."
            }
        else:
            print(f"Restored instance {restored_db_identifier} is not available for rename.")
            return {
                'statusCode': 202,
                'body': f"Restored instance {restored_db_identifier} is not available for rename. Please retry later."
            }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': str(e)
        }
