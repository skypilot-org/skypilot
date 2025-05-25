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
Check for config override and warn user
*/}}
{{- define "skypilot.checkConfigOverride" -}}
{{- if .Values.apiService.config -}}
{{- $isUpgrade := eq .Release.Revision 1 | not -}}
{{- if $isUpgrade -}}
{{- if not .Values.apiService.forceConfigOverride -}}
⚠️  CONFIG OVERRIDE WARNING ⚠️

You are upgrading the SkyPilot API server with 'apiService.config' set.
By default, the existing configuration in the persistent volume will be preserved and your ConfigMap config will be IGNORED.

If you want to override the existing configuration:
1. Get the current SkyPilot config:
   kubectl get configmap {{ .Release.Name }}-config -n {{ .Release.Namespace }} -o jsonpath='{.data.config\.yaml}' > current-config.yaml
2. Edit current-config.yaml with your desired changes
3. Run the upgrade command with:
   helm upgrade {{ .Release.Name }} --set-file apiService.config=current-config.yaml --set apiService.forceConfigOverride=true

For more information, see: https://docs.skypilot.co/en/latest/reference/api-server/api-server-upgrade.html

{{- end -}}
{{- else -}}
{{- if not .Values.apiService.forceConfigOverride -}}
⚠️  CONFIG OVERRIDE WARNING ⚠️

You are installing SkyPilot API server with 'apiService.config' set.
By default, if there is existing configuration in the persistent volume, your ConfigMap config will be IGNORED.

If you want to force the ConfigMap to override any existing configuration, add:
  --set apiService.forceConfigOverride=true

For more information about configuration management, see:
https://docs.skypilot.co/en/latest/reference/config.html

{{- end -}}
{{- end -}}
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
