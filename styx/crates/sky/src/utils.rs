//! Utility functions
//! Stub for now, will be expanded later

pub mod common {
    pub fn generate_cluster_name() -> String {
        format!("sky-{}", uuid::Uuid::new_v4().to_string()[..8])
    }
}
