## Rust-Portierungsstand

Dieser Ordner beherbergt die neue Rust-Codebasis, welche Schritt fuer Schritt
bestehende Python-Komponenten abloesen soll.

### Aktuelles Crate: `skypilot-utils`

- Enthaelt die Funktion `read_last_n_lines`, welche das Verhalten von
  `sky.utils.common_utils.read_last_n_lines` repliziert.
- Ausfuehrung der Tests:

  ```sh
  cd rust
  cargo test
  ```

### Geplanter Integrationspfad

1. Aufbau einer PyO3- oder FFI-Schnittstelle, damit Python direkt auf die
   Rust-Implementierung zugreifen kann.
2. Austausch existierender Python-Aufrufe von
   `sky.utils.common_utils.read_last_n_lines` gegen die neue Rust-Version.
3. Schrittweise Portierung weiterer Utilities, sobald die Schnittstelle
   stabil ist.

Bis zur vollstaendigen Integration laufen Python- und Rust-Implementierung
parallel; Tests innerhalb des Rust-Crates stellen sicher, dass das Verhalten
mit der bisherigen Python-Funktion uebereinstimmt.
