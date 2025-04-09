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