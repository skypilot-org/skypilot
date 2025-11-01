use stix_mcp::McpClient;
use anyhow::Result;

/// Example: Two agents collaborating on a file
#[tokio::main]
async fn main() -> Result<()> {
    let base_url = "http://localhost:8080".to_string();

    // Agent A starts working
    println!("=== Agent A registering ===");
    let mut agent_a = McpClient::new(base_url.clone());
    let agent_a_id = agent_a.register(
        "Agent-A".to_string(),
        vec![
            "LockFiles".to_string(),
            "UpdateProgress".to_string(),
            "SearchCodebase".to_string(),
        ],
    ).await?;
    println!("Agent A registered with ID: {}", agent_a_id);

    // Lock a file
    println!("\n=== Agent A locking file ===");
    let lock_result = agent_a.lock_file(
        "stix-core/src/state/db_store.rs".to_string(),
        Some(300), // 5 minutes
    ).await?;
    
    if lock_result.success {
        println!("✓ Agent A successfully locked db_store.rs");
    } else {
        println!("✗ Agent A failed to lock: {:?}", lock_result.error);
    }

    // Agent B tries to access the same file
    println!("\n=== Agent B registering ===");
    let mut agent_b = McpClient::new(base_url.clone());
    let agent_b_id = agent_b.register(
        "Agent-B".to_string(),
        vec![
            "LockFiles".to_string(),
            "SearchCodebase".to_string(),
        ],
    ).await?;
    println!("Agent B registered with ID: {}", agent_b_id);

    println!("\n=== Agent B trying to lock same file ===");
    let lock_result_b = agent_b.lock_file(
        "stix-core/src/state/db_store.rs".to_string(),
        None,
    ).await?;

    if !lock_result_b.success {
        println!("✓ Agent B correctly blocked: {}", lock_result_b.error.unwrap());
    }

    // Agent A searches codebase
    println!("\n=== Agent A searching codebase ===");
    let search_result = agent_a.search_codebase(
        "pub struct".to_string(),
        Some("*.rs".to_string()),
        true,
    ).await?;
    
    if search_result.success {
        println!("✓ Search completed successfully");
        if let Some(result) = search_result.result {
            println!("Found matches: {:?}", result.get("matches"));
        }
    }

    // Agent A creates a task
    println!("\n=== Agent A creating task ===");
    let task_result = agent_a.issue_task(
        "Review db_store.rs changes".to_string(),
        "Please review the changes made to the database store module".to_string(),
        Some("high".to_string()),
        Some("Agent-B".to_string()),
    ).await?;

    if task_result.success {
        println!("✓ Task created successfully");
    }

    // Agent A unlocks the file
    println!("\n=== Agent A unlocking file ===");
    let unlock_result = agent_a.unlock_file(
        "stix-core/src/state/db_store.rs".to_string(),
    ).await?;

    if unlock_result.success {
        println!("✓ Agent A successfully unlocked db_store.rs");
    }

    // Update progress
    println!("\n=== Agent A updating progress ===");
    let progress_result = agent_a.update_progress(
        "Database Integration".to_string(),
        "Completed implementation of db_store.rs with proper error handling".to_string(),
        true,
    ).await?;

    if progress_result.success {
        println!("✓ Progress updated successfully");
    }

    // Now Agent B can lock the file
    println!("\n=== Agent B trying to lock file again ===");
    let lock_result_b2 = agent_b.lock_file(
        "stix-core/src/state/db_store.rs".to_string(),
        Some(300),
    ).await?;

    if lock_result_b2.success {
        println!("✓ Agent B successfully locked db_store.rs");
        
        // Agent B unlocks
        agent_b.unlock_file("stix-core/src/state/db_store.rs".to_string()).await?;
        println!("✓ Agent B unlocked db_store.rs");
    }

    // View logs
    println!("\n=== Agent A viewing logs ===");
    let logs = agent_a.get_my_logs().await?;
    println!("Agent A has {} log entries", logs.len());

    println!("\n=== Collaboration example completed successfully! ===");

    Ok(())
}
