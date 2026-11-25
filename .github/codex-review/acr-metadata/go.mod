module github.com/jbaruch/coding-policy/acr-metadata

go 1.27.0

require (
	go.yaml.in/yaml/v3 v3.0.6
	// Renew alongside the ACR commit pinned in tests.yml and publish.yml.
	go.yaml.in/yaml/v4 v4.0.0
)
