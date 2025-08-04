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
Create the name of the service account to use
*/}}
{{- define "skypilot.serviceAccountName" -}}
{{- if .Values.rbac.serviceAccountName -}}
{{ .Values.rbac.serviceAccountName }}
{{- else -}}
{{ .Release.Name }}-api-sa
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
{{ printf "%s-initial-basic-auth" .Release.Name }}
{{- else -}}
{{- /* Return empty string */ -}}
{{ "" }}
{{- end -}}
{{- end -}}

{{/* API server start arguments */}}
{{- define "skypilot.apiArgs" -}}
--deploy{{ if include "skypilot.enableBasicAuthInAPIServer" . | trim | eq "true" }} --enable-basic-auth{{ end }}
{{- end -}}

{{/* Validate the oauth2 proxy based auth config */}}
{{- define "skypilot.validateOAuth2Config" -}}
{{- $oauth2ProxyConfig := .Values.auth.oauth2Proxy -}}
{{- $deployOAuth2Proxy := index .Values.ingress "oauth2-proxy" "enabled" -}}
{{- $baseUrl := $oauth2ProxyConfig.baseUrl -}}
{{- $oauth2ProxyAuthEnabled := $oauth2ProxyConfig.enabled -}}
{{/* External oauth2-proxy url and oauth2-proxy deployment are mutually exclusive */}}
{{- if and $baseUrl $deployOAuth2Proxy -}}
{{- fail "auth.oauth2Proxy.baseUrl and ingress.oauth2-proxy.enabled are mutually exclusive. Set only one of them." -}}
{{- end -}}
{{/* If oauth2-proxy base authentication is enabled, either an external url or an oauth2-proxy deployment must be configured */}}
{{- if $oauth2ProxyAuthEnabled -}}
{{- if not (or $baseUrl $deployOAuth2Proxy) -}}
{{- fail "When auth.oauth2Proxy.enabled is true, either auth.oauth2Proxy.baseUrl or ingress.oauth2-proxy.enabled must be set." -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "skypilot.oauth2ProxyURL" -}}
{{- include "skypilot.validateOAuth2Config" . }}
{{- if .Values.auth.oauth2Proxy.baseUrl -}}
{{ .Values.auth.oauth2Proxy.baseUrl }}
{{- else -}}
http://{{ .Release.Name }}-oauth2-proxy:4180
{{- end -}}
{{- end -}}

{{- define "skypilot.serviceAccountAuthEnabled" -}}
{{- if ne .Values.auth.serviceAccount.enabled nil -}}
{{- .Values.auth.serviceAccount.enabled -}}
{{- else -}}
{{- .Values.apiService.enableServiceAccounts -}}
{{- end -}}
{{- end -}}
