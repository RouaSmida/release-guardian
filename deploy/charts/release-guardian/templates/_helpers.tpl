{{/* All helpers take a dict: "name" (component name) and "root" (the chart root context). */}}

{{- define "rg.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{- define "rg.labels" -}}
{{ include "rg.selectorLabels" . }}
app.kubernetes.io/part-of: release-guardian
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ .root.Chart.Name }}-{{ .root.Chart.Version }}
{{- end }}

{{/* Labels for pods: selector labels plus the label the PodMonitor selects on. */}}
{{- define "rg.podLabels" -}}
{{ include "rg.selectorLabels" . }}
app.kubernetes.io/part-of: release-guardian
{{- end }}

{{/* Image reference. Extra dict key "tag" overrides global.imageTag when set. */}}
{{- define "rg.image" -}}
{{ .root.Values.global.imageRepository }}/{{ .name }}:{{ .tag | default .root.Values.global.imageTag }}
{{- end }}

{{/* Hardened container security context shared by every container. */}}
{{- define "rg.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "rg.podSecurityContext" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end }}
