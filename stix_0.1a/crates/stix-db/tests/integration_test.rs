use stix_db::{DbPool, Repository, TaskRow, EdgeRow};
use tempfile::NamedTempFile;
use time::OffsetDateTime;

#[tokio::test]
async fn test_migration_and_basic_crud() {
    // Create temporary database
    let temp_file = NamedTempFile::new().unwrap();
    let db_url = format!("sqlite://{}", temp_file.path().to_str().unwrap());

    // Connect and run migrations
    let pool = DbPool::connect(&db_url).await.unwrap();
    let repo = Repository::new(pool);

    // Test inserting a task
    let now = OffsetDateTime::now_utc();
    let task = TaskRow {
        id: "task1".to_string(),
        name: "Test Task".to_string(),
        status: "PENDING".to_string(),
        retries: 0,
        max_retries: 3,
        payload: Some(b"test payload".to_vec()),
        created_at: now.format(&time::format_description::well_known::Rfc3339).unwrap(),
        updated_at: now.format(&time::format_description::well_known::Rfc3339).unwrap(),
    };

    repo.insert_task(&task).await.unwrap();

    // Test retrieving the task
    let retrieved = repo.get_task("task1").await.unwrap().unwrap();
    assert_eq!(retrieved.id, "task1");
    assert_eq!(retrieved.name, "Test Task");
    assert_eq!(retrieved.status, "PENDING");
}

#[tokio::test]
async fn test_ready_tasks_query() {
    let temp_file = NamedTempFile::new().unwrap();
    let db_url = format!("sqlite://{}", temp_file.path().to_str().unwrap());

    let pool = DbPool::connect(&db_url).await.unwrap();
    let repo = Repository::new(pool);

    let now = OffsetDateTime::now_utc();
    let now_str = now.format(&time::format_description::well_known::Rfc3339).unwrap();

    // Insert tasks: A (SUCCESS), B (PENDING, depends on A), C (PENDING, no deps)
    let task_a = TaskRow {
        id: "A".to_string(),
        name: "Task A".to_string(),
        status: "SUCCESS".to_string(),
        retries: 0,
        max_retries: 3,
        payload: None,
        created_at: now_str.clone(),
        updated_at: now_str.clone(),
    };

    let task_b = TaskRow {
        id: "B".to_string(),
        name: "Task B".to_string(),
        status: "PENDING".to_string(),
        retries: 0,
        max_retries: 3,
        payload: None,
        created_at: now_str.clone(),
        updated_at: now_str.clone(),
    };

    let task_c = TaskRow {
        id: "C".to_string(),
        name: "Task C".to_string(),
        status: "PENDING".to_string(),
        retries: 0,
        max_retries: 3,
        payload: None,
        created_at: now_str.clone(),
        updated_at: now_str.clone(),
    };

    repo.insert_task(&task_a).await.unwrap();
    repo.insert_task(&task_b).await.unwrap();
    repo.insert_task(&task_c).await.unwrap();

    // Add dependency B -> A
    let edge = EdgeRow {
        src: "A".to_string(),
        dst: "B".to_string(),
    };
    repo.upsert_edge(&edge).await.unwrap();

    // Ready tasks should be B and C (A is SUCCESS, B depends on A which is SUCCESS, C has no deps)
    let ready = repo.get_ready_tasks(10).await.unwrap();
    let ready_ids: std::collections::HashSet<_> = ready.iter().map(|t| &t.id).collect();

    assert!(ready_ids.contains("B"));
    assert!(ready_ids.contains("C"));
    assert!(!ready_ids.contains("A"));
}

#[tokio::test]
async fn test_edge_consistency() {
    let temp_file = NamedTempFile::new().unwrap();
    let db_url = format!("sqlite://{}", temp_file.path().to_str().unwrap());

    let pool = DbPool::connect(&db_url).await.unwrap();
    let repo = Repository::new(pool);

    let now = OffsetDateTime::now_utc();
    let now_str = now.format(&time::format_description::well_known::Rfc3339).unwrap();

    // Insert task with PENDING status and dependency on non-existent task
    let task = TaskRow {
        id: "task1".to_string(),
        name: "Task 1".to_string(),
        status: "PENDING".to_string(),
        retries: 0,
        max_retries: 3,
        payload: None,
        created_at: now_str.clone(),
        updated_at: now_str.clone(),
    };

    repo.insert_task(&task).await.unwrap();

    let edge = EdgeRow {
        src: "nonexistent".to_string(),
        dst: "task1".to_string(),
    };
    repo.upsert_edge(&edge).await.unwrap();

    // Task should not be ready because dependency doesn't exist
    let ready = repo.get_ready_tasks(10).await.unwrap();
    assert!(ready.is_empty());
}

#[tokio::test]
async fn test_status_transitions() {
    let temp_file = NamedTempFile::new().unwrap();
    let db_url = format!("sqlite://{}", temp_file.path().to_str().unwrap());

    let pool = DbPool::connect(&db_url).await.unwrap();
    let repo = Repository::new(pool);

    let now = OffsetDateTime::now_utc();
    let now_str = now.format(&time::format_description::well_known::Rfc3339).unwrap();

    let task = TaskRow {
        id: "task1".to_string(),
        name: "Task 1".to_string(),
        status: "PENDING".to_string(),
        retries: 0,
        max_retries: 3,
        payload: None,
        created_at: now_str.clone(),
        updated_at: now_str.clone(),
    };

    repo.insert_task(&task).await.unwrap();

    // Update status
    repo.update_task_status("task1", "RUNNING", 0).await.unwrap();

    let updated = repo.get_task("task1").await.unwrap().unwrap();
    assert_eq!(updated.status, "RUNNING");
    assert_eq!(updated.retries, 0);
}

#[tokio::test]
async fn test_kv_store() {
    let temp_file = NamedTempFile::new().unwrap();
    let db_url = format!("sqlite://{}", temp_file.path().to_str().unwrap());

    let pool = DbPool::connect(&db_url).await.unwrap();
    let repo = Repository::new(pool);

    let value = b"test value";
    repo.set_kv("test_key", value).await.unwrap();

    let retrieved = repo.get_kv("test_key").await.unwrap().unwrap();
    assert_eq!(retrieved, value);

    repo.delete_kv("test_key").await.unwrap();
    let deleted = repo.get_kv("test_key").await.unwrap();
    assert!(deleted.is_none());
}
