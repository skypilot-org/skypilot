{{- define "skypilot.checkResources" -}}
{{- $cpu := .Values.apiService.resources.requests.cpu | default "0" -}}
{{- $memory := .Values.apiService.resources.requests.memory | default "0" -}}

{{- /* Convert CPU to numeric value */ -}}
{{- $cpuNum := 0.0 -}}
{{- if kindIs "string" $cpu -}}
  {{- if hasSuffix "m" $cpu -}}
    {{- $cpuNum = float64 (divf (trimSuffix "m" $cpu | atoi) 1000) -}}
  {{- else -}}
    {{- $cpuNum = float64 ($cpu | atoi) -}}
  {{- end -}}
{{- else -}}
  {{- $cpuNum = float64 $cpu -}}
{{- end -}}

{{- /* Convert memory to Gi */ -}}
{{- $memNum := 0.0 -}}
{{- if hasSuffix "Gi" $memory -}}
  {{- $memNum = float64 (trimSuffix "Gi" $memory | atoi) -}}
{{- else if hasSuffix "Mi" $memory -}}
  {{- $memNum = float64 (divf (trimSuffix "Mi" $memory | atoi) 1024) -}}
{{- else if hasSuffix "G" $memory -}}
  {{- $memNum = float64 ($memory | trimSuffix "G" | atoi) -}}
{{- else if hasSuffix "M" $memory -}}
  {{- $memNum = float64 (divf (trimSuffix "M" $memory | atoi) 1024) -}}
{{- end -}}

{{- if or (lt $cpuNum 4.0) (lt $memNum 8.0) -}}
{{/* TODO(aylei): add a reference to the tuning guide once complete */}}
  {{- fail "Error\nDeploying a SkyPilot API server requires at least 4 CPU cores and 8 GiB memory. You can either:\n1. Change `--set apiService.resources.requests.cpu` and `--set apiService.resources.requests.memory` to meet the requirements or unset them to use defaults\n2. add `--set apiService.skipResourceCheck=true` in command args to bypass this check (not recommended for production)\nto resolve this issue and then try again." -}}
{{- end -}}

{{- end -}}

{{/*
Resolve the image name, overriding the registry when global.imageRegistry is set.
Usage: {{ include "common.image" (dict "root" . "image" "repo/name:tag") }}
*/}}
{{- define "common.image" -}}
{{- $image := default "" .image -}}
{{- $registry := default "" .root.Values.global.imageRegistry -}}
{{- if $registry -}}
  {{- $imagePath := trimPrefix "/" $image -}}
  {{- $parts := splitList "/" $imagePath -}}
  {{- if gt (len $parts) 1 -}}
    {{- $first := index $parts 0 -}}
    {{- if or (contains "." $first) (contains ":" $first) (eq $first "localhost") -}}
      {{- $imagePath = join "/" (slice $parts 1) -}}
    {{- end -}}
  {{- end -}}
  {{- printf "%s/%s" (trimSuffix "/" $registry) $imagePath -}}
{{- else -}}
  {{- $image -}}
{{- end -}}
{{- end -}}

{{/*
Pod scheduling aligned with apiService (nodeSelector, affinity, tolerations).
Applied to oauth2-proxy and bundled redis so they schedule like the API server
when umbrella charts set apiService.nodeSelector / tolerations / affinity.
*/}}
{{- define "skypilot.apiPodScheduling" -}}
{{- with .Values.apiService.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
{{- end }}
{{- with .Values.apiService.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
{{- end }}
{{- with .Values.apiService.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
{{- end }}
{{- end -}}


{{/*
Check for apiService.config during upgrade and display warning
*/}}
{{- define "skypilot.checkUpgradeConfig" -}}
{{- if and .Release.IsUpgrade .Values.apiService.config -}}
WARNING: apiService.config is set during an upgrade operation, which will be IGNORED.

To update your SkyPilot config, follow the instructions in the upgrade guide:
https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html#setting-the-skypilot-config
{{- end -}}
{{- end -}}

{{/*
Compute full release name with optional fullnameOverride.
*/}}
{{- define "skypilot.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Create the name of the service account to use
*/}}
{{- define "skypilot.serviceAccountName" -}}
{{- if .Values.rbac.serviceAccountName -}}
{{ .Values.rbac.serviceAccountName }}
{{- else -}}
{{ include "skypilot.fullname" . }}-api-sa
{{- end -}}
{{- end -}}

{{/*
Create the namespace if not exist
*/}}
{{- define "skypilot.ensureNamespace" -}}
{{ if not (lookup "v1" "Namespace" "" .) }}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ . }}
  annotations:
    {{/* Keep the namespace when uninstalling the chart, so that the deployed sky resources (if any) can still work even if the API server get uninstalled */ -}}
    helm.sh/resource-policy: keep
{{ end -}}
{{- end -}}

{{/* Whether to enable basic auth */}}
{{- define "skypilot.enableBasicAuthInAPIServer" -}}
{{- if and (not (index .Values.ingress "oauth2-proxy" "enabled")) .Values.apiService.enableUserManagement -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/* Get initial basic auth secret name */}}
{{- define "skypilot.initialBasicAuthSecretName" -}}
{{- if .Values.apiService.initialBasicAuthSecret -}}
{{ .Values.apiService.initialBasicAuthSecret }}
{{- else if .Values.apiService.initialBasicAuthCredentials -}}
{{ printf "%s-initial-basic-auth" (include "skypilot.fullname" .) }}
{{- else -}}
{{- /* Return empty string */ -}}
{{ "" }}
{{- end -}}
{{- end -}}

{{/* API server start arguments */}}
{{- define "skypilot.apiArgs" -}}
--deploy{{ if include "skypilot.enableBasicAuthInAPIServer" . | trim | eq "true" }} --enable-basic-auth{{ end }}{{ if .Values.apiService.host }} --host={{ .Values.apiService.host }}{{ end }}
{{- end -}}

{{- define "skypilot.oauth2ProxyURL" -}}
http://{{ include "skypilot.fullname" . }}-oauth2-proxy:4180
{{- end -}}

{{/* oauth2-proxy HTTP bind address; mirrors apiService.host address family so an
     IPv6 (e.g. "::") deployment binds a wildcard the pod's IPv6 network can reach. */}}
{{- define "skypilot.oauth2HttpAddress" -}}
{{- if and .Values.apiService.host (contains ":" .Values.apiService.host) -}}
[::]:4180
{{- else -}}
0.0.0.0:4180
{{- end -}}
{{- end -}}

{{- define "skypilot.ingressBasicAuthEnabled" -}}
{{- if and .Values.ingress.enabled (or .Values.ingress.authSecret .Values.ingress.authCredentials) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{- define "skypilot.ingressOAuthEnabled" -}}
{{- if and .Values.ingress.enabled (index .Values.ingress "oauth2-proxy" "enabled") -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{- define "skypilot.serviceAccountAuthEnabled" -}}
{{- if include "skypilot.ingressBasicAuthEnabled" . | trim | eq "true" -}}
false
{{- else if and .Values.auth .Values.auth.serviceAccount (ne .Values.auth.serviceAccount.enabled nil) -}}
{{- .Values.auth.serviceAccount.enabled -}}
{{- else -}}
{{- .Values.apiService.enableServiceAccounts -}}
{{- end -}}
{{- end -}}

{{/* Validate the oauth config */}}
{{- define "skypilot.validateOAuthConfig" -}}
{{- $authOAuthEnabled := .Values.auth.oauth.enabled -}}
{{- $ingressBasicAuthEnabled := include "skypilot.ingressBasicAuthEnabled" . | trim | eq "true" -}}
{{- $ingressOAuthEnabled := include "skypilot.ingressOAuthEnabled" . | trim | eq "true" -}}

{{- if and $authOAuthEnabled $ingressBasicAuthEnabled -}}
  {{- fail "Error\nauth.oauth.enabled cannot be used together with ingress basic authentication (ingress.authSecret or ingress.authCredentials). These authentication methods are mutually exclusive. Please:\n1. Disable auth.oauth.enabled, OR\n2. Remove ingress.authSecret and ingress.authCredentials\nThen try again." -}}
{{- end -}}

{{- if and $authOAuthEnabled $ingressOAuthEnabled -}}
  {{- fail "Error\nauth.oauth.enabled cannot be used together with ingress OAuth2 proxy authentication (ingress.oauth2-proxy.enabled). These authentication methods are mutually exclusive. Please:\n1. Disable auth.oauth.enabled, OR\n2. Set ingress.oauth2-proxy.enabled to false\nThen try again." -}}
{{- end -}}
{{- end -}}

{{/* Validate the external proxy config */}}
{{- define "skypilot.validateExternalProxyConfig" -}}
{{- $externalProxyEnabled := .Values.auth.externalProxy.enabled -}}
{{- $authOAuthEnabled := .Values.auth.oauth.enabled -}}
{{- $ingressOAuthEnabled := include "skypilot.ingressOAuthEnabled" . | trim | eq "true" -}}

{{- if and $externalProxyEnabled $authOAuthEnabled -}}
  {{- fail "Error\nauth.externalProxy.enabled cannot be used together with auth.oauth.enabled. These authentication methods are mutually exclusive. Please:\n1. Disable auth.externalProxy.enabled, OR\n2. Set auth.oauth.enabled to false\nThen try again." -}}
{{- end -}}

{{- if and $externalProxyEnabled $ingressOAuthEnabled -}}
  {{- fail "Error\nauth.externalProxy.enabled cannot be used together with ingress.oauth2-proxy.enabled. These authentication methods are mutually exclusive. Please:\n1. Disable auth.externalProxy.enabled, OR\n2. Set ingress.oauth2-proxy.enabled to false\nThen try again." -}}
{{- end -}}
{{- end -}}

{{/*
Name of the ingress-nginx controller Service. The LoadBalancer Service in front
of Traefik keeps this name, so a release that switches controllers keeps its
external address. Mirrors the ingress-nginx chart's controller fullname.
*/}}
{{- define "skypilot.ingressControllerServiceName" -}}
{{- $ingressNginx := index .Values "ingress-nginx" -}}
{{- $base := "" -}}
{{- if $ingressNginx.fullnameOverride -}}
{{- $base = $ingressNginx.fullnameOverride -}}
{{- else -}}
{{- $name := default "ingress-nginx" $ingressNginx.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- $base = .Release.Name -}}
{{- else -}}
{{- $base = printf "%s-%s" .Release.Name $name -}}
{{- end -}}
{{- end -}}
{{- printf "%s-controller" ($base | trunc 63 | trimSuffix "-") | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Traefik subchart names, mirroring the Traefik chart's helpers: the pod
selector labels and the ServiceAccount name.
*/}}
{{- define "skypilot.traefikName" -}}
{{- default "traefik" .Values.traefik.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "skypilot.traefikInstance" -}}
{{- default (printf "%s-%s" .Release.Name .Release.Namespace) .Values.traefik.instanceLabelOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "skypilot.traefikServiceAccountName" -}}
{{- $traefik := .Values.traefik -}}
{{- $fullname := "" -}}
{{- if $traefik.fullnameOverride -}}
{{- $fullname = $traefik.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default "traefik" $traefik.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- $fullname = .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $fullname = printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- default $fullname (($traefik.serviceAccount).name) -}}
{{- end -}}

{{/*
Labels of the Services in front of Traefik: the ingress-nginx controller
Service's labels plus ingress-nginx.controller.service.labels.
Usage: include "skypilot.traefikServiceLabels" (dict "root" . "svc" $svc)
*/}}
{{- define "skypilot.traefikServiceLabels" -}}
app.kubernetes.io/name: ingress-nginx
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: controller
{{- with .svc.labels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Spec of a Service in front of Traefik. It renders the fields of the
ingress-nginx chart's controller Service templates (controller-service.yaml
and controller-service-internal.yaml, chart 4.11.8) with that chart's
defaults, so a release that switches controllers keeps its Services as they
were; only the selector and target ports change.
Usage: include "skypilot.traefikServiceSpec" (dict "root" . "svc" $svc "s" $settings "internal" bool)
where $svc is ingress-nginx.controller.service and $settings is $svc itself
or $svc.internal.
*/}}
{{- define "skypilot.traefikServiceSpec" -}}
{{- $svc := .svc -}}
{{- $s := .s | default dict -}}
{{- $mainType := $svc.type | default "LoadBalancer" -}}
{{- $type := $mainType -}}
{{- if .internal }}{{- $type = $s.type | default $mainType -}}{{- end -}}
type: {{ $type }}
{{- with $s.clusterIP }}
clusterIP: {{ . }}
{{- end }}
{{- with $s.externalIPs }}
externalIPs:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $s.loadBalancerIP }}
loadBalancerIP: {{ . }}
{{- end }}
{{- with $s.loadBalancerSourceRanges }}
loadBalancerSourceRanges:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $s.loadBalancerClass }}
loadBalancerClass: {{ . }}
{{- end }}
{{- if hasKey $s "allocateLoadBalancerNodePorts" }}
allocateLoadBalancerNodePorts: {{ $s.allocateLoadBalancerNodePorts }}
{{- end }}
{{- with $s.externalTrafficPolicy }}
externalTrafficPolicy: {{ . }}
{{- end }}
{{- with $s.sessionAffinity }}
sessionAffinity: {{ . }}
{{- end }}
{{- with $s.healthCheckNodePort }}
healthCheckNodePort: {{ . }}
{{- end }}
ipFamilyPolicy: {{ $s.ipFamilyPolicy | default "SingleStack" }}
ipFamilies:
  {{- toYaml ($s.ipFamilies | default (list "IPv4")) | nindent 2 }}
selector:
  app.kubernetes.io/name: {{ include "skypilot.traefikName" .root }}
  app.kubernetes.io/instance: {{ include "skypilot.traefikInstance" .root }}
{{- /* ingress-nginx decides node ports from the main Service type for both Services. */}}
{{- $setNodePorts := or (eq $mainType "NodePort") (eq $mainType "LoadBalancer") }}
{{- $ports := $s.ports | default dict }}
{{- $mainPorts := $svc.ports | default dict }}
{{- $nodePorts := $s.nodePorts | default dict }}
{{- $appProtocol := dig "appProtocol" true $s }}
ports:
{{- if dig "enableHttp" true $svc }}
  - name: http
    port: {{ $ports.http | default $mainPorts.http | default 80 }}
    protocol: TCP
    targetPort: web
    {{- if $appProtocol }}
    appProtocol: http
    {{- end }}
    {{- if and $setNodePorts $nodePorts.http }}
    nodePort: {{ $nodePorts.http }}
    {{- end }}
{{- end }}
{{- if dig "enableHttps" true $svc }}
  - name: https
    port: {{ $ports.https | default $mainPorts.https | default 443 }}
    protocol: TCP
    targetPort: websecure
    {{- if $appProtocol }}
    appProtocol: https
    {{- end }}
    {{- if and $setNodePorts $nodePorts.https }}
    nodePort: {{ $nodePorts.https }}
    {{- end }}
{{- end }}
{{- end -}}
