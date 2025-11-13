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
RDS_PASSWORD="${RDS_PASSWORD:-$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)}"
RDS_REGION="${AWS_REGION:-us-east-2}"
RDS_INSTANCE_CLASS="${RDS_INSTANCE_CLASS:-db.t3.micro}"
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

# Check if RDS instance already exists
if aws rds describe-db-instances --region "$RDS_REGION" --db-instance-identifier "$RDS_INSTANCE_ID" >/dev/null 2>&1; then
    echo "RDS instance $RDS_INSTANCE_ID already exists, deleting it first..." >&2
    aws rds delete-db-instance \
        --region "$RDS_REGION" \
        --db-instance-identifier "$RDS_INSTANCE_ID" \
        --skip-final-snapshot \
        $AWS_NO_PAGER
    echo "Waiting for instance to be deleted..." >&2
    aws rds wait db-instance-deleted \
        --region "$RDS_REGION" \
        --db-instance-identifier "$RDS_INSTANCE_ID"
fi

# Get VPC and subnets - use EKS VPC if available, otherwise use default VPC
if [ -n "$EKS_VPC_CONFIG" ]; then
    echo "Using EKS VPC configuration from EKS_VPC_CONFIG..." >&2
    # Parse VPC ID from YAML format: "  id: vpc-xxx" (under vpc:)
    # Look for lines with "id:" that contain "vpc-" pattern
    VPC_ID=$(echo "$EKS_VPC_CONFIG" | grep -E "^\s+id:\s+vpc-" | awk '{print $2}' | tr -d '"' | tr -d "'" | head -n1)

    if [ -z "$VPC_ID" ]; then
        echo "WARNING: Could not parse VPC ID from EKS_VPC_CONFIG, falling back to default VPC" >&2
        USE_EKS_VPC=false
    else
        # Parse subnet IDs from YAML format
        # Format: "        id: subnet-xxx" (nested under subnets/public/)
        # Look for lines with "id:" that contain "subnet-" pattern
        SUBNET_IDS=$(echo "$EKS_VPC_CONFIG" | grep -E "^\s+id:\s+subnet-" | awk '{print $2}' | tr -d '"' | tr -d "'")

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

# Get default security group for the VPC
SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
    --region "$RDS_REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=default" \
    --query "SecurityGroups[0].GroupId" \
    --output text)

# Use first 2 subnets (either from EKS config or default VPC)
SUBNET_1="${SUBNET_ARRAY[0]}"
SUBNET_2="${SUBNET_ARRAY[1]}"

# Create DB subnet group (required for RDS, needs at least 2 subnets in different AZs)
# Use fixed subnet group name based on instance ID
DB_SUBNET_GROUP_NAME="skypilot-test-subnet-group-${RDS_INSTANCE_ID}"
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
        $AWS_NO_PAGER
fi

# Create RDS PostgreSQL instance
echo "Creating RDS PostgreSQL instance..." >&2
aws rds create-db-instance \
    --region "$RDS_REGION" \
    --db-instance-identifier "$RDS_INSTANCE_ID" \
    --db-instance-class "$RDS_INSTANCE_CLASS" \
    --engine postgres \
    --engine-version "14.9" \
    --master-username "$RDS_USERNAME" \
    --master-user-password "$RDS_PASSWORD" \
    --allocated-storage 20 \
    --storage-type gp2 \
    --db-name "$RDS_DB_NAME" \
    --vpc-security-group-ids "$SECURITY_GROUP_ID" \
    --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
    --publicly-accessible \
    --backup-retention-period 0 \
    $AWS_NO_PAGER

echo "Waiting for RDS instance to be available (this may take several minutes)..." >&2
aws rds wait db-instance-available \
    --region "$RDS_REGION" \
    --db-instance-identifier "$RDS_INSTANCE_ID"

# Get RDS endpoint and port
RDS_ENDPOINT=$(aws rds describe-db-instances \
    --region "$RDS_REGION" \
    --db-instance-identifier "$RDS_INSTANCE_ID" \
    --query "DBInstances[0].Endpoint.Address" \
    --output text)
RDS_PORT=$(aws rds describe-db-instances \
    --region "$RDS_REGION" \
    --db-instance-identifier "$RDS_INSTANCE_ID" \
    --query "DBInstances[0].Endpoint.Port" \
    --output text)

echo "RDS Endpoint: $RDS_ENDPOINT" >&2
echo "RDS Port: $RDS_PORT" >&2

# Construct and output connection URI (to stdout, not stderr)
SKYPILOT_DB_CONNECTION_URI="postgresql://${RDS_USERNAME}:${RDS_PASSWORD}@${RDS_ENDPOINT}:${RDS_PORT}/${RDS_DB_NAME}"
echo "$SKYPILOT_DB_CONNECTION_URI"

# Also export instance ID and subnet group name for cleanup if needed
echo "RDS_INSTANCE_ID=$RDS_INSTANCE_ID" >&2
echo "DB_SUBNET_GROUP_NAME=$DB_SUBNET_GROUP_NAME" >&2
