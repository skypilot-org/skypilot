#!/bin/bash
# Script to create an AWS RDS PostgreSQL database for SkyPilot testing
# Outputs the connection URI to stdout in the format: postgresql://user:pass@host:port/db

set -e  # Exit on error
set -o pipefail  # Exit on pipe failure

# Configuration
# Use fixed instance name - can be overridden via RDS_INSTANCE_ID env var
RDS_INSTANCE_ID="${RDS_INSTANCE_ID:-skypilot-large-production-test-db}"
RDS_DB_NAME="${RDS_DB_NAME:-skypilot}"
RDS_USERNAME="${RDS_USERNAME:-skypilot}"
# Use fixed password for test instances to allow reuse, or allow override via env var
RDS_PASSWORD="${RDS_PASSWORD:-skypilot-test-password-12345}"
RDS_REGION="${AWS_REGION:-us-east-2}"
# Use db.m5.xlarge (4 vCPU, 16GB RAM) to match GCP db-custom-4-15360 (4 CPU, 15GB RAM)
RDS_INSTANCE_CLASS="${RDS_INSTANCE_CLASS:-db.m5.xlarge}"
DB_SUBNET_GROUP_NAME=""

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo "ERROR: AWS CLI is not installed or not in PATH" >&2
    exit 1
fi

# Check AWS CLI version and set pager option accordingly
# --no-cli-pager is only available in AWS CLI v2
AWS_CLI_VERSION=$(aws --version 2>&1 | grep -oE 'aws-cli/[0-9]+\.[0-9]+' | cut -d'/' -f2 || echo "1")
AWS_CLI_MAJOR_VERSION=$(echo "$AWS_CLI_VERSION" | cut -d'.' -f1)
if [ "$AWS_CLI_MAJOR_VERSION" -ge 2 ]; then
    AWS_NO_PAGER="--no-cli-pager"
else
    AWS_NO_PAGER=""
fi

echo "Creating AWS RDS PostgreSQL database..." >&2
echo "RDS Instance ID: $RDS_INSTANCE_ID" >&2
echo "RDS Region: $RDS_REGION" >&2
echo "Starting database creation process..." >&2

# Check if RDS instance already exists
echo "Checking if RDS instance already exists..." >&2
INSTANCE_EXISTS=false
if aws rds describe-db-instances --region "$RDS_REGION" --db-instance-identifier "$RDS_INSTANCE_ID" >/dev/null 2>&1; then
    INSTANCE_STATUS=$(aws rds describe-db-instances \
        --region "$RDS_REGION" \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --query "DBInstances[0].DBInstanceStatus" \
        --output text)
    echo "RDS instance $RDS_INSTANCE_ID exists with status: $INSTANCE_STATUS" >&2

    # If instance is being deleted, wait for deletion to complete
    if [ "$INSTANCE_STATUS" = "deleting" ]; then
        echo "Instance is currently being deleted. Waiting for deletion to complete..." >&2
        echo "This may take several minutes..." >&2
        if aws rds wait db-instance-deleted --region "$RDS_REGION" --db-instance-identifier "$RDS_INSTANCE_ID" 2>&1; then
            echo "✓ Instance deletion completed" >&2
            INSTANCE_EXISTS=false
        else
            echo "ERROR: Failed to wait for instance deletion" >&2
            exit 1
        fi
    else
        INSTANCE_EXISTS=true
        echo "Reusing existing instance..." >&2

        # Get VPC and subnet group from existing instance
        VPC_ID=$(aws rds describe-db-instances \
            --region "$RDS_REGION" \
            --db-instance-identifier "$RDS_INSTANCE_ID" \
            --query "DBInstances[0].DBSubnetGroup.VpcId" \
            --output text)
        DB_SUBNET_GROUP_NAME=$(aws rds describe-db-instances \
            --region "$RDS_REGION" \
            --db-instance-identifier "$RDS_INSTANCE_ID" \
            --query "DBInstances[0].DBSubnetGroup.DBSubnetGroupName" \
            --output text)
        echo "Using VPC: $VPC_ID from existing instance" >&2
    fi
fi

# Get VPC and subnets if instance doesn't exist - use EKS VPC if available, otherwise use default VPC
if [ "$INSTANCE_EXISTS" = "false" ]; then
if [ -n "$EKS_VPC_CONFIG_PRIVATE" ]; then
    echo "Using custom VPC configuration from EKS_VPC_CONFIG_PRIVATE..." >&2
    # Convert literal \n to actual newlines for parsing
    VPC_CONFIG_FOR_PARSING=$(printf '%b\n' "$EKS_VPC_CONFIG_PRIVATE")
    # Parse VPC ID from YAML format: "  id: vpc-xxx" (under vpc:)
    # Look for lines with "id:" that contain "vpc-" pattern
    VPC_ID=$(echo "$VPC_CONFIG_FOR_PARSING" | grep -E "^\s+id:\s+vpc-" | awk '{print $2}' | tr -d '"' | tr -d "'" | head -n1)

    if [ -z "$VPC_ID" ]; then
        echo "WARNING: Could not parse VPC ID from EKS_VPC_CONFIG_PRIVATE, falling back to default VPC" >&2
        USE_EKS_VPC=false
    else
        # Parse subnet IDs from YAML format
        # Format: "        id: subnet-xxx" (nested under subnets/public/)
        # Look for lines with "id:" that contain "subnet-" pattern
        SUBNET_IDS=$(echo "$VPC_CONFIG_FOR_PARSING" | grep -E "^\s+id:\s+subnet-" | awk '{print $2}' | tr -d '"' | tr -d "'")

        # Convert to array
        SUBNET_ARRAY=($SUBNET_IDS)

        if [ ${#SUBNET_ARRAY[@]} -lt 2 ]; then
            echo "WARNING: EKS VPC config has less than 2 subnets (found ${#SUBNET_ARRAY[@]}), falling back to default VPC" >&2
            USE_EKS_VPC=false
        else
            # Validate subnets exist in the RDS_REGION and are in different availability zones
            if ! aws ec2 describe-subnets --region "$RDS_REGION" --subnet-ids "${SUBNET_ARRAY[0]}" >/dev/null 2>&1; then
                echo "WARNING: EKS VPC subnets are not in region $RDS_REGION, falling back to default VPC" >&2
                USE_EKS_VPC=false
            else
                # Verify subnets are in different availability zones
                # Get availability zones for the first 2 subnets (RDS requires at least 2 in different AZs)
                SUBNET_AZS=$(aws ec2 describe-subnets --region "$RDS_REGION" --subnet-ids "${SUBNET_ARRAY[0]}" "${SUBNET_ARRAY[1]}" --query 'Subnets[*].AvailabilityZone' --output text 2>/dev/null)
                # Count unique availability zones (handle both space and tab separated)
                UNIQUE_AZS=$(echo "$SUBNET_AZS" | tr '\t' ' ' | tr ' ' '\n' | grep -v '^$' | sort -u | wc -l)

                if [ "$UNIQUE_AZS" -lt 2 ]; then
                    echo "WARNING: EKS VPC subnets are not in different availability zones (found $UNIQUE_AZS unique AZs: $SUBNET_AZS), falling back to default VPC" >&2
                    USE_EKS_VPC=false
                else
                    USE_EKS_VPC=true
                    echo "Using EKS VPC: $VPC_ID with ${#SUBNET_ARRAY[@]} subnets in region $RDS_REGION (AZs: $SUBNET_AZS)" >&2
                fi
            fi
        fi
    fi
else
    USE_EKS_VPC=false
    echo "Using default VPC configuration (EKS_VPC_CONFIG_PRIVATE not set)..." >&2
fi

if [ "$USE_EKS_VPC" != "true" ]; then
    # Get default VPC and security group for RDS
    echo "Getting default VPC and security group..." >&2
    VPC_ID=$(aws ec2 describe-vpcs --region "$RDS_REGION" --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)
    if [ "$VPC_ID" == "None" ] || [ -z "$VPC_ID" ]; then
        echo "ERROR: No default VPC found in region $RDS_REGION" >&2
        exit 1
    fi

    # Get subnets in the default VPC (RDS requires at least 2 subnets in different AZs)
    echo "Getting subnets for DB subnet group..." >&2
    SUBNET_IDS=$(aws ec2 describe-subnets \
        --region "$RDS_REGION" \
        --filters "Name=vpc-id,Values=$VPC_ID" \
        --query "Subnets[*].SubnetId" \
        --output text)

    # Convert to array and take first 2 subnets
    SUBNET_ARRAY=($SUBNET_IDS)
    if [ ${#SUBNET_ARRAY[@]} -lt 2 ]; then
        echo "ERROR: Need at least 2 subnets in different availability zones for RDS" >&2
        exit 1
    fi
fi

fi  # End of "if instance doesn't exist" block for VPC setup

# For existing instances, get the subnet info
if [ "$INSTANCE_EXISTS" = "true" ]; then
    SUBNET_IDS=$(aws rds describe-db-subnet-groups \
        --region "$RDS_REGION" \
        --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
        --query "DBSubnetGroups[0].Subnets[*].SubnetIdentifier" \
        --output text 2>/dev/null || echo "")
    SUBNET_ARRAY=($SUBNET_IDS)
fi

# Get default security group for the VPC (needed for both new and existing instances)
SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
    --region "$RDS_REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=default" \
    --query "SecurityGroups[0].GroupId" \
    --output text)

# Add inbound rule for PostgreSQL (port 5432) if it doesn't exist
echo "Configuring security group to allow PostgreSQL connections..." >&2
# Check if rule already exists
EXISTING_RULE=$(aws ec2 describe-security-groups \
    --region "$RDS_REGION" \
    --group-ids "$SECURITY_GROUP_ID" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`5432\` && ToPort==\`5432\` && IpProtocol==\`tcp\`]" \
    --output text 2>/dev/null || echo "")

if [ -z "$EXISTING_RULE" ]; then
    echo "Adding inbound rule for PostgreSQL (port 5432) from anywhere (0.0.0.0/0)..." >&2
    aws ec2 authorize-security-group-ingress \
        --region "$RDS_REGION" \
        --group-id "$SECURITY_GROUP_ID" \
        --protocol tcp \
        --port 5432 \
        --cidr 0.0.0.0/0 \
        $AWS_NO_PAGER >&2 || echo "Note: Rule may already exist or failed to add" >&2
else
    echo "PostgreSQL port 5432 rule already exists in security group" >&2
fi

# Use first 2 subnets (either from EKS config or default VPC)
SUBNET_1="${SUBNET_ARRAY[0]}"
SUBNET_2="${SUBNET_ARRAY[1]}"

# DB subnet group name
DB_SUBNET_GROUP_NAME="skypilot-test-subnet-group-${RDS_INSTANCE_ID}"

# Create custom parameter group with increased max_connections (for both new and existing instances)
PARAM_GROUP_NAME="skypilot-test-pg-${RDS_INSTANCE_ID}"
echo "Setting up parameter group with increased max_connections..." >&2
if aws rds describe-db-parameter-groups --region "$RDS_REGION" --db-parameter-group-name "$PARAM_GROUP_NAME" >/dev/null 2>&1; then
    echo "Parameter group $PARAM_GROUP_NAME already exists, reusing it..." >&2
else
    echo "Creating parameter group: $PARAM_GROUP_NAME" >&2
    aws rds create-db-parameter-group \
        --region "$RDS_REGION" \
        --db-parameter-group-name "$PARAM_GROUP_NAME" \
        --db-parameter-group-family postgres17 \
        --description "Custom parameter group for SkyPilot test RDS instance with increased max_connections" \
        $AWS_NO_PAGER >&2
fi

# Set max_connections to 250 (recommended: at least 215 for 215 workers)
echo "Setting max_connections to 250 in parameter group..." >&2
aws rds modify-db-parameter-group \
    --region "$RDS_REGION" \
    --db-parameter-group-name "$PARAM_GROUP_NAME" \
    --parameters "ParameterName=max_connections,ParameterValue=250,ApplyMethod=pending-reboot" \
    $AWS_NO_PAGER >&2 || echo "Note: Parameter may already be set or parameter group is being applied" >&2
echo "✓ Parameter group configured with max_connections=250" >&2

# Initialize NEEDS_REBOOT flag
NEEDS_REBOOT=false

# Only create resources if instance doesn't exist
if [ "$INSTANCE_EXISTS" = "false" ]; then
    # Create DB subnet group (required for RDS, needs at least 2 subnets in different AZs)
    echo "Creating DB subnet group: $DB_SUBNET_GROUP_NAME with subnets: $SUBNET_1, $SUBNET_2" >&2
    # Check if subnet group already exists
    if aws rds describe-db-subnet-groups --region "$RDS_REGION" --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" >/dev/null 2>&1; then
        echo "DB subnet group $DB_SUBNET_GROUP_NAME already exists, reusing it..." >&2
    else
        aws rds create-db-subnet-group \
            --region "$RDS_REGION" \
            --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
            --db-subnet-group-description "Subnet group for SkyPilot test RDS instance" \
            --subnet-ids "$SUBNET_1" "$SUBNET_2" \
            $AWS_NO_PAGER >&2
    fi

    # Create RDS PostgreSQL instance
    echo "Creating RDS PostgreSQL instance..." >&2
    aws rds create-db-instance \
        --region "$RDS_REGION" \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --db-instance-class "$RDS_INSTANCE_CLASS" \
        --engine postgres \
        --master-username "$RDS_USERNAME" \
        --master-user-password "$RDS_PASSWORD" \
        --allocated-storage 20 \
        --storage-type gp2 \
        --db-name "$RDS_DB_NAME" \
        --vpc-security-group-ids "$SECURITY_GROUP_ID" \
        --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
        --db-parameter-group-name "$PARAM_GROUP_NAME" \
        --publicly-accessible \
        --backup-retention-period 0 \
        $AWS_NO_PAGER >&2
    # New instances don't need reboot - parameter group is applied at creation
    NEEDS_REBOOT=false
else
    # For existing instances, check if parameter group needs to be applied
    CURRENT_PARAM_GROUP=$(aws rds describe-db-instances \
        --region "$RDS_REGION" \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --query "DBInstances[0].DBParameterGroups[0].DBParameterGroupName" \
        --output text 2>/dev/null || echo "")

    if [ "$CURRENT_PARAM_GROUP" != "$PARAM_GROUP_NAME" ]; then
        echo "Applying parameter group $PARAM_GROUP_NAME to existing instance..." >&2
        aws rds modify-db-instance \
            --region "$RDS_REGION" \
            --db-instance-identifier "$RDS_INSTANCE_ID" \
            --db-parameter-group-name "$PARAM_GROUP_NAME" \
            --apply-immediately \
            $AWS_NO_PAGER >&2 || echo "Note: Parameter group may already be applied or instance is modifying" >&2
        NEEDS_REBOOT=true
    else
        echo "Instance already using parameter group $PARAM_GROUP_NAME" >&2
        # Check if parameters are pending reboot
        PENDING_PARAMS=$(aws rds describe-db-instances \
            --region "$RDS_REGION" \
            --db-instance-identifier "$RDS_INSTANCE_ID" \
            --query "DBInstances[0].PendingModifiedValues" \
            --output json 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); print('yes' if d and 'DBParameterGroupName' in d else 'no')" 2>/dev/null || echo "no")
        if [ "$PENDING_PARAMS" = "yes" ]; then
            NEEDS_REBOOT=true
        else
            NEEDS_REBOOT=false
        fi
    fi
fi

echo "Waiting for RDS instance to be available (this may take several minutes)..." >&2
if ! aws rds wait db-instance-available \
    --region "$RDS_REGION" \
    --db-instance-identifier "$RDS_INSTANCE_ID" 2>&1; then
    echo "ERROR: Failed to wait for RDS instance to become available" >&2
    exit 1
fi

# Reboot instance if parameter group was applied (max_connections requires reboot)
if [ "$NEEDS_REBOOT" = "true" ]; then
    echo "Rebooting instance to apply parameter group changes (max_connections requires reboot)..." >&2
    INSTANCE_STATUS=$(aws rds describe-db-instances \
        --region "$RDS_REGION" \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --query "DBInstances[0].DBInstanceStatus" \
        --output text)

    if [ "$INSTANCE_STATUS" = "available" ]; then
        aws rds reboot-db-instance \
            --region "$RDS_REGION" \
            --db-instance-identifier "$RDS_INSTANCE_ID" \
            $AWS_NO_PAGER >&2
        echo "Waiting for instance to finish rebooting..." >&2
        aws rds wait db-instance-available \
            --region "$RDS_REGION" \
            --db-instance-identifier "$RDS_INSTANCE_ID"
        echo "✓ Instance rebooted, max_connections=250 is now active" >&2
    else
        echo "Instance is $INSTANCE_STATUS, will reboot automatically when ready" >&2
        aws rds wait db-instance-available \
            --region "$RDS_REGION" \
            --db-instance-identifier "$RDS_INSTANCE_ID"
        # Instance may have rebooted during the wait, verify
        echo "✓ Instance is available, max_connections=250 should be active" >&2
    fi
fi

echo "RDS instance status is 'available', getting endpoint information..." >&2

# Get RDS endpoint and port
if ! RDS_ENDPOINT=$(aws rds describe-db-instances \
    --region "$RDS_REGION" \
    --db-instance-identifier "$RDS_INSTANCE_ID" \
    --query "DBInstances[0].Endpoint.Address" \
    --output text 2>&1); then
    echo "ERROR: Failed to get RDS endpoint: $RDS_ENDPOINT" >&2
    exit 1
fi
if ! RDS_PORT=$(aws rds describe-db-instances \
    --region "$RDS_REGION" \
    --db-instance-identifier "$RDS_INSTANCE_ID" \
    --query "DBInstances[0].Endpoint.Port" \
    --output text 2>&1); then
    echo "ERROR: Failed to get RDS port: $RDS_PORT" >&2
    exit 1
fi

echo "RDS Endpoint: $RDS_ENDPOINT" >&2
echo "RDS Port: $RDS_PORT" >&2

# Test connectivity to ensure endpoint is ready
echo "Testing connectivity to RDS endpoint..." >&2
MAX_RETRIES=30
RETRY_COUNT=0
CONNECTION_READY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if timeout 5 nc -zv "$RDS_ENDPOINT" "$RDS_PORT" >/dev/null 2>&1; then
        CONNECTION_READY=true
        echo "✓ RDS endpoint is accepting connections" >&2
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
        echo "Waiting for endpoint to accept connections (attempt $RETRY_COUNT/$MAX_RETRIES)..." >&2
        sleep 2
    fi
done

if [ "$CONNECTION_READY" != "true" ]; then
    echo "ERROR: RDS endpoint is not accepting connections after $MAX_RETRIES attempts" >&2
    echo "Please check security groups and network ACLs" >&2
    exit 1
fi

# Construct and output connection URI (to stdout, not stderr)
# URL-encode username and password to handle special characters
if ! ENCODED_USERNAME=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$RDS_USERNAME', safe=''))" 2>&1); then
    echo "ERROR: Failed to URL-encode username: $ENCODED_USERNAME" >&2
    exit 1
fi
if ! ENCODED_PASSWORD=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$RDS_PASSWORD', safe=''))" 2>&1); then
    echo "ERROR: Failed to URL-encode password" >&2
    exit 1
fi
# Add SSL parameter for RDS PostgreSQL (required for remote connections)
SKYPILOT_DB_CONNECTION_URI="postgresql://${ENCODED_USERNAME}:${ENCODED_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/${RDS_DB_NAME}?sslmode=require"
echo "$SKYPILOT_DB_CONNECTION_URI"

# Verify max_connections is set to 250
echo "Verifying max_connections setting..." >&2
MAX_CONN_VERIFIED=false
MAX_RETRIES=10
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    ACTUAL_MAX_CONN=$(python3 << PYEOF 2>/dev/null
import psycopg2
from psycopg2 import OperationalError
try:
    conn = psycopg2.connect(
        host="$RDS_ENDPOINT",
        port=$RDS_PORT,
        user="$RDS_USERNAME",
        password="$RDS_PASSWORD",
        database="$RDS_DB_NAME",
        sslmode='require',
        connect_timeout=5
    )
    cur = conn.cursor()
    cur.execute("SHOW max_connections;")
    result = cur.fetchone()[0]
    print(result)
    cur.close()
    conn.close()
except Exception as e:
    print("ERROR")
PYEOF
)

    if [ "$ACTUAL_MAX_CONN" = "250" ]; then
        MAX_CONN_VERIFIED=true
        echo "✓ Verified: max_connections is set to 250" >&2
        break
    elif [ "$ACTUAL_MAX_CONN" != "ERROR" ] && [ -n "$ACTUAL_MAX_CONN" ]; then
        echo "Current max_connections: $ACTUAL_MAX_CONN (expected 250)" >&2
        if [ $RETRY_COUNT -lt $((MAX_RETRIES - 1)) ]; then
            echo "Waiting for parameter to take effect..." >&2
            sleep 3
        fi
    else
        if [ $RETRY_COUNT -lt $((MAX_RETRIES - 1)) ]; then
            echo "Connection failed, retrying..." >&2
            sleep 2
        fi
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ "$MAX_CONN_VERIFIED" != "true" ]; then
    echo "WARNING: Could not verify max_connections=250. Current value may be: $ACTUAL_MAX_CONN" >&2
    echo "The parameter group is configured correctly, but the instance may need more time to apply changes." >&2
fi

# Also export instance ID and subnet group name for cleanup if needed
echo "RDS_INSTANCE_ID=$RDS_INSTANCE_ID" >&2
echo "DB_SUBNET_GROUP_NAME=$DB_SUBNET_GROUP_NAME" >&2
