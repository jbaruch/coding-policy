// acr-metadata projects the review authority fields of one ACR manifest from
// stdin to JSON. Its input is a Git blob supplied by the fleet context reader.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"unicode/utf8"

	"go.yaml.in/yaml/v3"
)

type metadata struct {
	Name       string   `json:"name"`
	Repository string   `json:"repository"`
	Rules      []string `json:"rules"`
}

func project(input io.Reader) (metadata, error) {
	var manifest struct {
		SchemaVersion int    `yaml:"schemaVersion"`
		Name          string `yaml:"name"`
		Source        struct {
			Repository string `yaml:"repository"`
		} `yaml:"source"`
		Artifacts yaml.Node `yaml:"artifacts"`
	}

	decoder := yaml.NewDecoder(input)
	// Do not pass syntax errors through to logs. They can quote the manifest.
	if err := decoder.Decode(&manifest); err != nil {
		return metadata{}, errors.New("repair the YAML declaration")
	}
	var extra yaml.Node
	if err := decoder.Decode(&extra); err != io.EOF {
		return metadata{}, errors.New("provide exactly one YAML document")
	}
	if manifest.SchemaVersion != 1 {
		return metadata{}, errors.New("declare schemaVersion: 1")
	}

	result := metadata{Name: manifest.Name, Repository: manifest.Source.Repository, Rules: []string{}}
	if !utf8.ValidString(result.Name) || !utf8.ValidString(result.Repository) {
		return metadata{}, errors.New("use valid UTF-8 in name and source.repository")
	}
	if manifest.Artifacts.IsZero() {
		return result, nil
	}

	// Nodes distinguish missing fields from explicit null, including aliases and merges.
	var artifacts *struct {
		Rules yaml.Node `yaml:"rules"`
	}
	if err := manifest.Artifacts.Decode(&artifacts); err != nil || artifacts == nil {
		return metadata{}, errors.New("ACR artifacts must be a mapping; repair the plugin manifest")
	}
	if artifacts.Rules.IsZero() {
		return result, nil
	}

	// Pointer entries retain nulls so they cannot silently vanish from the sequence.
	var entries []*struct {
		Path string `yaml:"path"`
	}
	if err := artifacts.Rules.Decode(&entries); err != nil || entries == nil {
		return metadata{}, errors.New("declare ACR rules as a list of artifacts with paths")
	}
	for _, entry := range entries {
		if entry == nil {
			return metadata{}, errors.New("declare ACR rules as a list of artifacts with paths")
		}
		if !utf8.ValidString(entry.Path) {
			return metadata{}, errors.New("use valid UTF-8 in ACR rule paths")
		}
		result.Rules = append(result.Rules, entry.Path)
	}
	return result, nil
}

func main() {
	result, err := project(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, "could not write ACR metadata JSON; check the output stream")
		os.Exit(1)
	}
}
