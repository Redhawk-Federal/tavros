# Grafana Stack as an Optional Metrics Platform

- Status: proposed
- Deciders: Tavros platform team
- Date: 2026-09-30

Technical Story: complements [ADR-0016](0016-elastic-cloud-for-observability-data-aggregation-and-visualization.md)
and [ADR-0017](0017-jaeger-for-tracing-with-elasticsearch-backend.md). Numbered after ADR-0030
(OpenTelemetry for distributed tracing), which is on the `modernization-phase-2026-5` branch.

## Context and Problem Statement

The platform collects logs, node metrics and uptime with Elastic Beats, and traces with Jaeger on
Elasticsearch. Nothing collects Prometheus metrics, even though Kong, Kuma, Flux, cert-manager,
Keycloak, Nexus and the Camel applications all expose them. Because of this there are no alerts, and
no golden signals for the Kong edge or the mesh. On clusters without a Kong Enterprise licence, the
Jaeger UI is also unreachable, since it depends on the EE `openid-connect` plugin.

How should Tavros offer a metrics and alerting option without breaking installations that rely on
the Elastic stack?

## Decision Drivers

- Close the Prometheus gap for the components the platform already ships.
- Opt-in: installations that do not enable it must render exactly the same manifests.
- Single sign-on through Keycloak without depending on Kong EE.
- Follow the existing patterns: Flux HelmRelease (ADR-0023), sealed secrets (ADR-0008), one role per
  component (ADR-0024).

## Considered Options

1. kube-prometheus-stack (Prometheus Operator, Prometheus, Alertmanager, Grafana) as an optional role.
2. SigNoz (OpenTelemetry-native, ClickHouse).
3. Keep Elastic only and add Metricbeat Prometheus modules.

## Decision Outcome

Chosen option: **1, kube-prometheus-stack as the optional `grafana_stack` role**. It covers metrics and
alerting on day one. Kong and Kuma publish dashboards for it. Grafana OSS supports Keycloak through
generic OAuth, so it does not need Kong EE.

- The role is disabled by default (`grafana_stack.enabled: false`) and independent of `elastic_cloud`/`jaeger`.
- The role owns the scrape configuration for other components (PodMonitors, a Kuma `ScrapeConfig`
  via MADS, `MeshMetric`, `KongClusterPlugin prometheus`). Component roles therefore stay unchanged
  when the option is off.
- Prometheus and Alertmanager get no Ingress; only Grafana is exposed.

### Positive Consequences

- Kong, Kuma data planes and control plane, Flux, cert-manager and cluster metrics, with about 130
  default alert rules.
- Grafana login through Keycloak client roles (`admin`/`editor`/`viewer`).

### Negative Consequences

- A second observability stack to operate while both are enabled.
- Grafana, Loki and Tempo are AGPLv3 (Prometheus is Apache 2.0). Review this before redistributing
  a modified Tavros.
- Logs are optional (`grafana_stack.loki.enabled`): Loki runs as a single binary with one replica and
  filesystem storage, and Grafana Alloy collects the pod logs. It is not highly available and its
  retention is bound by the volume size; move Loki to object storage to scale out.
- Prometheus, Alertmanager and Grafana also run with one replica each.
- Control-plane components (controller-manager, scheduler, etcd, kube-proxy) are not scraped by
  default, because kOps and kind bind them to localhost (`grafana_stack.scrape_control_plane`).

## Pros and Cons of the Options

### SigNoz

- Good, because logs, metrics and traces live in one OpenTelemetry-native product.
- Bad, because OIDC SSO is Enterprise-only, so a Keycloak login would need Kong EE in front.
- Bad, because it adds ClickHouse and ZooKeeper to operate.

### Elastic only

- Good, because it adds no new component.
- Bad, because there is no Prometheus-native alerting, and the upstream Kong/Kuma dashboards and
  ServiceMonitors cannot be reused.

## Links

- Follow-ups: traces (Tempo + OpenTelemetry Collector, replacing the legacy Kuma `TrafficTrace`),
  application metrics (`/actuator/prometheus` in the Camel archetypes).
