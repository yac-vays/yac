{{/*
Expand the name of the chart.
*/}}
{{- define "name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label value.
*/}}
{{- define "chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Derive YAC_ROOT_PATH from the first ingress host's first path (or "/" if
ingress is disabled / no path is configured).
*/}}
{{- define "rootPath" -}}
{{- $root := "/" -}}
{{- if and .Values.ingress.enabled .Values.ingress.hosts -}}
  {{- $h := index .Values.ingress.hosts 0 -}}
  {{- if $h.paths -}}
    {{- $p := index $h.paths 0 -}}
    {{- if $p.path -}}
      {{- $root = $p.path -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- $root -}}
{{- end }}
