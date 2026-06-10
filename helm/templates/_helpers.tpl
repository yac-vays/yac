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

{{/*
Name of the bundled Redis Service / StatefulSet (mode=single).
*/}}
{{- define "redisName" -}}
{{- printf "%s-redis" (include "fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Resolved Redis URL passed to the YAC pod as YAC_ENV__REDIS_URL.
- mode=single   -> derived from the bundled Service.
- mode=external -> user-supplied .Values.redis.url (validated below).
Validates the mode value and the external URL early with `fail`.
*/}}
{{- define "redisUrl" -}}
{{- $mode := .Values.redis.mode | default "single" -}}
{{- if eq $mode "single" -}}
{{- if .Values.redis.password -}}
redis://:{{ .Values.redis.password | urlquery }}@{{ include "redisName" . }}:6379/0
{{- else -}}
redis://{{ include "redisName" . }}:6379/0
{{- end -}}
{{- else if eq $mode "external" -}}
{{- if not .Values.redis.url -}}
{{- fail "redis.mode=external requires redis.url to be set" -}}
{{- end -}}
{{- .Values.redis.url -}}
{{- else -}}
{{- fail (printf "redis.mode=%q is not supported (use \"single\" or \"external\")" $mode) -}}
{{- end -}}
{{- end }}
