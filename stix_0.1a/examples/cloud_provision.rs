//! Cloud Provisioning Example
//!
//! Provision instances on AWS, GCP, or Kubernetes.

use styx_cloud::{AwsProvider, GcpProvider, KubernetesProvider, CloudProvider, ProvisionRequest};
use styx_core::ResourceRequirements;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    println!("?? Cloud Provisioning Example\n");

    // Define resource requirements
    let resources = ResourceRequirements::new()
        .with_cpu(4.0)
        .with_memory(16.0);

    // Example 1: AWS
    println!("1?? AWS Provisioning:");
    let aws = AwsProvider::new().await?;
    
    let aws_request = ProvisionRequest::new("my-aws-instance", resources.clone())
        .with_region("us-west-2")
        .with_tag("project", "styx-demo");

    let aws_instance = aws.provision(aws_request).await?;
    println!("  ? AWS Instance: {} ({})", aws_instance.id, aws_instance.instance_type);

    // Example 2: GCP
    println!("\n2?? GCP Provisioning:");
    let gcp = GcpProvider::new("my-project-id").await?;
    
    let gcp_request = ProvisionRequest::new("my-gcp-instance", resources.clone())
        .with_region("us-central1");

    let gcp_instance = gcp.provision(gcp_request).await?;
    println!("  ? GCP Instance: {} ({})", gcp_instance.id, gcp_instance.instance_type);

    // Example 3: Kubernetes
    println!("\n3?? Kubernetes Provisioning:");
    let k8s = KubernetesProvider::new().await?;
    
    let k8s_request = ProvisionRequest::new("my-pod", resources.clone());

    let pod = k8s.provision(k8s_request).await?;
    println!("  ? Kubernetes Pod: {} ({})", pod.id, pod.name);

    // List all instances
    println!("\n?? Listing instances:");
    let aws_instances = aws.list_instances().await?;
    let gcp_instances = gcp.list_instances().await?;
    let k8s_pods = k8s.list_instances().await?;

    println!("  AWS: {} instances", aws_instances.len());
    println!("  GCP: {} instances", gcp_instances.len());
    println!("  K8s: {} pods", k8s_pods.len());

    Ok(())
}
