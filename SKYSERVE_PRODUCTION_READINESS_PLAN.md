# SkyServe Production Readiness Plan

## Executive Summary

Based on analysis of **248 SkyServe-related Linear tickets** across **12 enterprise customers** (Shopify, Valeo, Amazon, Nebius, Hippocratic, CoreWeave, Salk, Betterpic, Achira, CADDI, Orlando Magic, Lease Bio), this document outlines a prioritized roadmap to make SkyServe production-ready and achieve significant OSS community impact.

### Key Findings

| Metric | Count |
|--------|-------|
| Total SkyServe tickets | 248 |
| Active tickets (Todo/In Progress/Blocked) | 30+ |
| Enterprise customers with requests | 12 |
| GitHub-sourced tickets | 174 |
| Slack-sourced tickets | 61 |

### Top Request Categories

| Category | Total | Active | Most Requested By |
|----------|-------|--------|-------------------|
| Security & Auth | 74 | 11 | Shopify, OSS users |
| Load Balancer & Networking | 49 | 7 | Valeo, all users |
| Autoscaling | 18 | 8 | Enterprise users |
| High Availability | 14 | 1 | Nebius, Shopify |
| Multi-cloud/K8s | 5 | 1 | Enterprise users |

---

## Customer Request Analysis

### Tier 1: Major Enterprise Customers

#### Shopify (11 tickets) - PRIORITY: CRITICAL
**Status**: Major customer, Important tier

| Ticket | Request | Priority | Status |
|--------|---------|----------|--------|
| SKY-4286 | Multi-provider serving router (external APIs + self-hosted) | Medium | Todo |
| SKY-2691 | Token auth on LB instead of replicas | Medium | Todo |
| SKY-2692 | Readiness probe with bearer token | Urgent | Done |
| SKY-2690 | SSL key files relative path support | Urgent | Done |
| SKY-3380 | PVC with serve replicas | High | Blocked |
| SKY-3149 | Memory leak / OOM in API server | Urgent | Done |
| SKY-3501 | SSH keystroke latency | High | Done |

**Key Quote from Shopify (Ming Zhu)**:
> "We want SkyPilot to do for serving what it already does for compute - be a multi-cloud control plane. Specifying multiple endpoints: here's my base 10, here's my fireworks endpoints, here's my self-hosted endpoint - and SkyPilot as intelligent router choosing optimal provider."

**Shopify's North Star Feature**: Multi-provider routing across self-hosted + external APIs (OpenAI, Fireworks)

---

#### Nebius (12 tickets) - PRIORITY: HIGH
**Status**: Active enterprise user, K8s focus

| Ticket | Request | Priority | Status |
|--------|---------|----------|--------|
| SKY-3386 | HA serve controller disk_size issue | High | Todo |
| SKY-3380 | PVC fails with serve replicas | High | Blocked |
| SKY-1571 | High performance networking (Infiniband) | Medium | In Progress |
| SKY-3313 | Nebius K8s autoscaling | Medium | Paused |
| SKY-3389 | Laggy SSH connection | Medium | Paused |

---

#### Valeo (2 tickets) - PRIORITY: HIGH
**Status**: Restarting staging phase

| Ticket | Request | Priority | Status |
|--------|---------|----------|--------|
| SKY-4295 | Multiple service paths in ingress | Urgent | In Progress |
| SKY-3830 | Autoscaling in multi-context K8s | Urgent | Done |

---

#### Amazon (9 tickets) - PRIORITY: HIGH
**Status**: Active enterprise user

| Ticket | Request | Priority | Status |
|--------|---------|----------|--------|
| SKY-2545 | Batch processing queue of workloads | High | Paused |
| SKY-3389 | Laggy SSH connection | Medium | Paused |
| SKY-3504 | `sky api cancel -a` hangs | High | Done |
| SKY-3363 | Optimize dashboard/infra page loading | Medium | Paused |

---

#### Hippocratic (5 tickets) - PRIORITY: MEDIUM
**Status**: CoreWeave user, B200 deployments

| Ticket | Request | Priority | Status |
|--------|---------|----------|--------|
| SKY-3061 | Support CoreWeave autoscaler | Urgent | Done |
| SKY-3313 | Nebius K8s autoscaling | Medium | Paused |

---

### Tier 2: Batch Processing Customers (7 companies in single ticket)

**Ticket**: SKY-2545 - Best practice for batch processing workloads

| Customer | Use Case |
|----------|----------|
| Salk | Large genomic data processing |
| Amazon | Checkpoint conversion to HuggingFace format |
| Betterpic | Task queues for image processing |
| Achira | N workers across clouds pulling work off queue |
| CADDI | Autoscale inference based on SQS/Pub-Sub queue |
| Orlando Magic | Cartesian product for parallel job deployment |
| Lease Bio | Batch inference runner |

---

## Most Requested Features (by frequency and impact)

### 1. Security & Authentication (74 tickets)

**Active Issues**:
- SKY-2691: Token auth on LB (Shopify request)
- SKY-3696: Load balancer HTTP exceptions
- SKY-3852: Guides for internal network
- SKY-1458: HTTPS regression fix

**Backlog Highlights**:
- SKY-170: TLS/HTTPS for load balancer (20+ user requests in comments)
- SKY-202: API Authentication + HTTPS + production web server
- SKY-291: Log scrubbing for sensitive data
- SKY-229: Security groups for serve nodes

### 2. Load Balancer & Networking (49 tickets)

**Active Issues**:
- SKY-3767: Status offline during provisioning (UX blocker)
- SKY-4286: Multi-provider router (Shopify North Star)
- SKY-3386: HA controller disk_size issue

**Backlog Highlights**:
- SKY-311: Expose multiple ports
- SKY-279: Multiple ports in SkyServe
- SKY-114: AWS CloudFront/Route53 integration

### 3. LLM Serving (18 tickets)

**Active Issues**:
- SKY-3911: llm-d distributed inference support
- SKY-1351: Prefix Caching (KubeAI competitive feature)
- SKY-1571: High-performance networking (Infiniband)
- SKY-1814: Heterogeneous workloads (PD disaggregation)

**Backlog Highlights**:
- SKY-292: TensorRT-LLM and Triton examples
- SKY-2640: Reduce cold start time

### 4. Autoscaling (18 tickets)

**Active Issues**:
- SKY-3380: PVC fails with replicas (Blocked)
- SKY-2866: Autoscaling pool
- SKY-3332: FAILED_PROVISION bloating status

**Backlog Highlights**:
- SKY-293: Scale based on response time (not just QPS)
- SKY-659: Autoscaling by metrics

---

## Production Readiness Roadmap

### Phase 1: Quick Wins (1-2 weeks each)

These address immediate customer blockers and have high impact-to-effort ratio.

#### 1.1 Token Authentication at Load Balancer
**Ticket**: SKY-2691
**Customer**: Shopify
**Impact**: HIGH - Enables production deployments without relying on replica-level auth
**Effort**: LOW (2-3 days)

**Implementation**:
```python
# sky/serve/load_balancer.py
async def validate_api_key(request: Request):
    auth_header = request.headers.get("Authorization")
    expected_key = os.environ.get("SKYSERVE_API_KEY")
    if expected_key and (not auth_header or auth_header != f"Bearer {expected_key}"):
        raise HTTPException(status_code=401, detail="Invalid API key")
```

**Files to modify**:
- `sky/serve/load_balancer.py`
- `sky/serve/service_spec.py` (add auth config)
- `sky/serve/serve_utils.py` (template generation)

---

#### 1.2 Fix `sky serve status` During Provisioning
**Ticket**: SKY-3767
**Customer**: Multiple users
**Impact**: HIGH - Major UX issue causing users to think service is broken
**Effort**: LOW (1-2 days)

**Root Cause**: Controller tries to fetch replica endpoint through LB, but K8s without LB setup causes endpoint fetching to hang.

**Fix**:
1. Add timeout to `_query_ports_for_loadbalancer`
2. Force `podip` when no LB setup detected

---

#### 1.3 HTTPS Regression Fix
**Ticket**: SKY-1458
**Status**: In Review
**Impact**: MEDIUM - Blocking HTTPS users
**Effort**: LOW (already in review)

---

#### 1.4 Speed Up `sky serve status`
**Ticket**: SKY-3924
**Impact**: MEDIUM - UX improvement
**Effort**: LOW (2-3 days)

**Implementation**: Cache controller responses, reduce polling frequency.

---

### Phase 2: Core Production Features (2-4 weeks each)

#### 2.1 Prometheus Metrics Integration
**Ticket**: SKY-1183 (Metrics from controller/LB)
**Impact**: VERY HIGH - Required for production monitoring
**Effort**: MEDIUM (2 weeks)

**Metrics to Export**:
```python
# Request metrics
request_latency_seconds = Histogram('skyserve_request_latency_seconds',
    'Request latency', ['service', 'replica'])
request_count = Counter('skyserve_request_count',
    'Request count', ['service', 'replica', 'status'])
requests_in_flight = Gauge('skyserve_requests_in_flight',
    'Requests in flight', ['service', 'replica'])

# Replica metrics
replica_status = Gauge('skyserve_replica_status',
    'Replica status', ['service', 'replica', 'status'])
replica_qps = Gauge('skyserve_replica_qps',
    'Replica QPS', ['service', 'replica'])

# Autoscaler metrics
autoscaler_decisions = Counter('skyserve_autoscaler_decisions',
    'Autoscaler decisions', ['service', 'action'])
```

**Files to modify**:
- `sky/serve/load_balancer.py` - Add metrics collection
- `sky/serve/controller.py` - Expose /metrics endpoint
- `sky/serve/autoscalers.py` - Add decision metrics

---

#### 2.2 Multi-Provider Serving Router
**Ticket**: SKY-4286
**Customer**: Shopify (North Star feature)
**Impact**: VERY HIGH - Unique differentiator, no OSS competitor does this
**Effort**: HIGH (4-6 weeks)

**Architecture**:
```
┌─────────────────────────────────────────────────────────────┐
│                    SkyServe Router                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Intelligent Load Balancer               │   │
│  │  - Cost optimization                                 │   │
│  │  - Availability tracking                             │   │
│  │  - Latency-based routing                             │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│     ┌─────────────────────┼─────────────────────┐          │
│     ▼                     ▼                     ▼          │
│  ┌──────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │ Self-    │     │   External   │     │   External   │   │
│  │ Hosted   │     │   Provider   │     │   Provider   │   │
│  │ (SkyPilot│     │   (OpenAI)   │     │  (Fireworks) │   │
│  │ Replicas)│     │              │     │              │   │
│  └──────────┘     └──────────────┘     └──────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**YAML Extension**:
```yaml
service:
  providers:
    - type: skypilot
      name: self-hosted
      weight: 70
      replicas: 2
      resources:
        accelerators: H100:1
    - type: external
      name: openai
      endpoint: https://api.openai.com/v1
      api_key: ${OPENAI_API_KEY}
      weight: 20
      fallback: true
    - type: external
      name: fireworks
      endpoint: https://api.fireworks.ai/inference/v1
      api_key: ${FIREWORKS_API_KEY}
      weight: 10
      fallback: true
  routing:
    strategy: cost_optimized  # or: latency_optimized, availability_first
    fallback_chain: [self-hosted, fireworks, openai]
```

---

#### 2.3 Prefix Caching Support
**Ticket**: SKY-1351
**Impact**: HIGH - Competitive with KubeAI
**Effort**: MEDIUM (2-3 weeks)

**Implementation**:
- Hash-based routing in load balancer
- Route similar prompts to same replica
- Integration with vLLM's prefix caching

---

#### 2.4 llm-d / Distributed Inference Support
**Ticket**: SKY-3911
**Impact**: HIGH - Growing demand for disaggregated serving
**Effort**: HIGH (4-6 weeks)

**Approach**:
- Deploy llm-d components as separate SkyServe services
- SkyPilot handles cross-region orchestration
- Consider sidecar support for Envoy proxy

---

### Phase 3: Enterprise Features (1-2 months each)

#### 3.1 Fix PVC with Serve Replicas
**Ticket**: SKY-3380
**Status**: BLOCKED
**Customer**: Nebius, Shopify
**Impact**: HIGH - Required for stateful workloads

---

#### 3.2 HA Controller Without PVC
**Ticket**: SKY-3704
**Impact**: HIGH - Required for consolidation mode

---

#### 3.3 Multi-K8s SkyServe
**Ticket**: SKY-1193
**Impact**: MEDIUM - Enterprise use case

---

#### 3.4 Batch Processing Framework
**Ticket**: SKY-2545
**Customers**: Amazon, Salk, Betterpic, Achira, CADDI, Orlando Magic, Lease Bio
**Impact**: HIGH - 7 customers requesting similar functionality

---

## OSS Community Impact Strategy

### 1. Unique Differentiators

| Feature | Competitor Status | SkyServe Opportunity |
|---------|------------------|---------------------|
| Multi-provider router | None have it | First mover advantage |
| Prefix caching | KubeAI advertises it | Match + exceed |
| Multi-cloud serving | vLLM/SGLang single cloud | Already have it |
| Spot instance serving | Limited support elsewhere | Already have it |

### 2. Marketing Opportunities

1. **Blog Post**: "SkyServe: The First Multi-Cloud LLM Gateway"
   - Announce multi-provider routing
   - Show cost savings examples

2. **Blog Post**: "Prefix Caching in SkyServe"
   - Counter KubeAI's marketing
   - Show performance benchmarks

3. **Tutorial**: "Production-Ready LLM Serving in 5 Minutes"
   - One-line TLS + Auth setup
   - Prometheus metrics out of the box

### 3. Quick OSS Wins

| Task | Impact | Effort |
|------|--------|--------|
| Add vLLM + TensorRT-LLM examples | HIGH | LOW |
| Document Python SDK for SkyServe | MEDIUM | LOW |
| Add Grafana dashboard template | HIGH | MEDIUM |
| Create "awesome-skyserve" repo | MEDIUM | LOW |

---

## Implementation Priority Matrix

```
                    HIGH IMPACT
                         │
    ┌────────────────────┼────────────────────┐
    │                    │                    │
    │  Multi-provider    │   Token Auth       │
    │  Router (4-6w)     │   (3d)             │
    │                    │                    │
    │  Prefix Caching    │   Metrics          │
    │  (3w)              │   (2w)             │
    │                    │                    │
LOW ├────────────────────┼────────────────────┤ HIGH
EFFORT                   │                    EFFORT
    │                    │                    │
    │  Fix serve status  │   llm-d            │
    │  (2d)              │   (6w)             │
    │                    │                    │
    │  HTTPS fix         │   Multi-K8s        │
    │  (1d)              │   (4w)             │
    │                    │                    │
    └────────────────────┼────────────────────┘
                         │
                    LOW IMPACT
```

---

## Recommended Sprint Plan

### Sprint 1 (Weeks 1-2): Quick Wins
- [ ] SKY-2691: Token auth on LB
- [ ] SKY-3767: Fix serve status offline
- [ ] SKY-1458: Merge HTTPS fix
- [ ] SKY-3924: Speed up status command

### Sprint 2 (Weeks 3-4): Observability
- [ ] SKY-1183: Prometheus metrics
- [ ] Create Grafana dashboard template
- [ ] Add structured logging

### Sprint 3-4 (Weeks 5-8): Multi-Provider Router
- [ ] SKY-4286: Design doc
- [ ] SKY-4286: Core implementation
- [ ] SKY-4286: External provider adapters
- [ ] SKY-4286: Documentation + examples

### Sprint 5-6 (Weeks 9-12): LLM Performance
- [ ] SKY-1351: Prefix caching
- [ ] SKY-3911: llm-d initial support
- [ ] Performance benchmarks

### Sprint 7-8 (Weeks 13-16): Enterprise
- [ ] SKY-3380: PVC fix
- [ ] SKY-3704: HA without PVC
- [ ] SKY-1193: Multi-K8s

---

## Success Metrics

| Metric | Current | Target (6 months) |
|--------|---------|-------------------|
| Active SkyServe enterprise users | 5 | 15 |
| GitHub stars (serve-related issues) | N/A | 50+ |
| Production deployments | Unknown | 100+ |
| Avg. time to production | Days | Hours |
| Documentation completeness | 60% | 95% |

---

## Appendix: All Active SkyServe Tickets

### Urgent Priority
- SKY-4295: Multiple service paths in ingress (Valeo) - In Progress
- SKY-3976: Pool interface for batch inference - Todo
- SKY-4018: JobGroup concept - Todo

### High Priority
- SKY-3696: Load balancer HTTP exceptions - Todo
- SKY-3767: Status offline during provisioning - Todo
- SKY-3911: llm-d support - Todo
- SKY-3386: HA controller disk_size - Todo
- SKY-3380: PVC with replicas - Blocked
- SKY-3704: Serve state without PVC - Todo
- SKY-2601: Consolidation Mode resources - In Review
- SKY-2866: Autoscaling pool - In Review
- SKY-3087: Cancel log background process - Todo

### Medium Priority
- SKY-4286: Multi-provider router (Shopify) - Todo
- SKY-2691: Token auth on LB - Todo
- SKY-1351: Prefix caching - Todo
- SKY-1571: High performance networking - In Progress
- SKY-3852: Internal network guides - Todo
- SKY-3924: Speed up serve status - Todo
- SKY-3332: FAILED_PROVISION bloating - Todo
- SKY-1192: K8s HA controller docs - Todo
- SKY-1193: Multi-K8s SkyServe - Todo
- SKY-1194: Cold start docs - Todo
- SKY-1190: Serve controller version updates - Todo

---

*Generated: 2026-01-10*
*Based on Linear ticket analysis of 248 SkyServe-related issues*
