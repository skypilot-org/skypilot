'use client';

import React, {
  useState,
  useEffect,
  useRef,
  useMemo,
  useCallback,
} from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import yaml from 'js-yaml';
import { CheckIcon } from 'lucide-react';
import { getWorkspaces, createWorkspace } from '@/data/connectors/workspaces';
import {
  invalidateWorkspaceCaches,
  workspaceNameError,
  workspaceNameTaken,
  workspaceNameTakenMessage,
} from '@/hooks/useWorkspacesConfig';
import { usePluginFlowSteps } from '@/plugins/PluginProvider';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { YamlEditor } from '@/components/ui/yaml-editor';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import { Stepper } from '@/components/elements/Stepper';

// The flow plugins register steps for; see `registerFlowStep` in
// PluginProvider for the step contract.
export const CREATE_WORKSPACE_FLOW = 'workspaces.create';

const NAME_STEP = { id: 'name', label: 'Name', order: 10 };
const CONFIGURE_STEP = { id: 'configure', label: 'Configure', order: 30 };

function newWorkspaceYaml(name) {
  return `${name}:\n  # New workspace configuration\n  # Leave empty to use all accessible infrastructure\n`;
}

const isMapping = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

// `patch` wins; mappings merge recursively, everything else is replaced.
function deepMerge(base, patch) {
  if (!isMapping(base) || !isMapping(patch)) return patch;
  const merged = { ...base };
  Object.entries(patch).forEach(([key, value]) => {
    merged[key] = deepMerge(base[key], value);
  });
  return merged;
}

// The YAML the Configure step starts from: the commented template for an
// empty config, else the config the earlier steps contributed.
function seedYaml(name, config) {
  if (Object.keys(config).length === 0) return newWorkspaceYaml(name);
  return yaml.dump(
    { [name]: config },
    { indent: 2, lineWidth: -1, noRefs: true, skipInvalid: true }
  );
}

// Points the YAML's top-level key at `newName` after the name changed on the
// Name step. An untouched template is regenerated; an edited one keeps its
// edits and comments and only has its key line rewritten.
function rekeyYaml(text, oldName, newName) {
  if (!oldName || text === newWorkspaceYaml(oldName)) {
    return newWorkspaceYaml(newName);
  }
  const escaped = oldName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return text.replace(new RegExp(`^${escaped}:`, 'm'), `${newName}:`);
}

// Parses the editor text into the config to create the workspace with, or an
// error for the editor to show.
function parseWorkspaceYaml(text, name) {
  let parsed;
  try {
    parsed = yaml.load(text) || {};
  } catch (err) {
    return { error: `Invalid YAML: ${err.message}` };
  }
  if (!isMapping(parsed)) {
    return {
      error: `The YAML must be a mapping with "${name}" as its top-level key.`,
    };
  }
  const keys = Object.keys(parsed);
  if (keys.length === 0) {
    return { config: {} };
  }
  if (keys.length > 1) {
    return {
      error: `Configuration must contain only one workspace. Found: ${keys.join(', ')}`,
    };
  }
  if (keys[0] !== name) {
    return {
      error: `The top-level key must stay "${name}" (found "${keys[0]}"). To use a different name, go back to the Name step.`,
    };
  }
  const config = parsed[name] || {};
  if (!isMapping(config)) {
    return { error: `The configuration under "${name}" must be a mapping.` };
  }
  if (Object.prototype.hasOwnProperty.call(config, 'name')) {
    return {
      error:
        'Remove "name" from the YAML. The workspace is named on the Name step.',
    };
  }
  return { config };
}

// Renders a plugin step with a change handler that is stable for its id.
function PluginStep({ step, workspaceName, value, onStepChange }) {
  const { id, component: Component } = step;
  const onChange = useCallback(
    (update) => onStepChange(id, update),
    [id, onStepChange]
  );
  return (
    <Component
      workspaceName={workspaceName}
      value={value}
      onChange={onChange}
    />
  );
}

// Creation flow: pick a name, go through any steps plugins add, then write
// the workspace's YAML. Plugin steps contribute to that YAML and get to act
// (`commit`) when the workspace is created; nothing is created before that.
export function NewWorkspace() {
  const router = useRouter();
  const [currentId, setCurrentId] = useState(NAME_STEP.id);
  const [workspaceName, setWorkspaceName] = useState('');
  const [existingWorkspaces, setExistingWorkspaces] = useState({});
  const [loading, setLoading] = useState(true);
  const [yamlValue, setYamlValue] = useState('');
  const [workspaceConfig, setWorkspaceConfig] = useState({});
  const [yamlError, setYamlError] = useState(null);
  // The name and contributed config the YAML was last seeded for.
  const seedRef = useRef({ name: null, patchKey: null });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const [commitErrors, setCommitErrors] = useState(null);
  const [success, setSuccess] = useState(null);

  // Plugin steps: which apply (null until every check has answered), and
  // each one's draft, readiness and config contribution.
  const pluginSteps = usePluginFlowSteps(CREATE_WORKSPACE_FLOW);
  const [applicable, setApplicable] = useState(null);
  const [stepState, setStepState] = useState({});

  useEffect(() => {
    let cancelled = false;
    getWorkspaces()
      .then((workspaces) => {
        if (!cancelled) setExistingWorkspaces(workspaces);
      })
      .catch((err) => {
        console.error('Failed to fetch existing workspaces:', err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // A step that says nothing, or whose check fails, is left out. Re-run
  // only when the set of registered steps changes.
  const pluginStepsKey = pluginSteps.map((step) => step.id).join('|');
  useEffect(() => {
    let cancelled = false;
    Promise.all(
      pluginSteps.map(async (step) => {
        if (!step.isApplicable) return [step.id, true];
        try {
          return [step.id, (await step.isApplicable()) === true];
        } catch (err) {
          console.error(
            `Flow step "${step.id}" applicability check failed:`,
            err
          );
          return [step.id, false];
        }
      })
    ).then((entries) => {
      if (!cancelled) setApplicable(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pluginStepsKey]);

  const activePluginSteps = useMemo(
    () => (applicable ? pluginSteps.filter((step) => applicable[step.id]) : []),
    [pluginSteps, applicable]
  );
  const steps = useMemo(
    () =>
      [NAME_STEP, ...activePluginSteps, CONFIGURE_STEP].sort(
        (a, b) => a.order - b.order
      ),
    [activePluginSteps]
  );
  const stepIndex = Math.max(
    0,
    steps.findIndex((step) => step.id === currentId)
  );
  const currentStep = steps[stepIndex];
  const nextStep = steps[stepIndex + 1] || null;
  const isPluginStep =
    currentStep.id !== NAME_STEP.id && currentStep.id !== CONFIGURE_STEP.id;

  const typedName = workspaceName.trim();
  const nameError = typedName ? workspaceNameError(typedName) : null;
  const taken = typedName
    ? workspaceNameTaken(existingWorkspaces, typedName)
    : null;
  const isWorkspaceNameTaken = taken !== null;
  const isNameValid = Boolean(typedName) && !nameError && !isWorkspaceNameTaken;

  // Everything the plugin steps contribute, in step order.
  const contributedConfig = useMemo(
    () =>
      activePluginSteps.reduce(
        (acc, step) => deepMerge(acc, stepState[step.id]?.configPatch || {}),
        {}
      ),
    [activePluginSteps, stepState]
  );
  const patchKey = JSON.stringify(contributedConfig);

  const onStepChange = useCallback((id, update) => {
    setStepState((prev) => ({ ...prev, [id]: { ...prev[id], ...update } }));
  }, []);

  const applyYaml = (text, name) => {
    setYamlValue(text);
    const { config, error: parseError } = parseWorkspaceYaml(text, name);
    setYamlError(parseError || null);
    if (!parseError) setWorkspaceConfig(config);
  };

  // A changed contribution reseeds the YAML (hand edits are lost); a changed
  // name alone only moves the key.
  const enterConfigure = () => {
    const seed = seedRef.current;
    if (seed.patchKey !== patchKey) {
      applyYaml(seedYaml(typedName, contributedConfig), typedName);
      seedRef.current = { name: typedName, patchKey };
    } else if (seed.name !== typedName) {
      applyYaml(rekeyYaml(yamlValue, seed.name, typedName), typedName);
      seedRef.current = { ...seed, name: typedName };
    }
  };

  const canLeaveCurrent =
    currentStep.id === NAME_STEP.id
      ? isNameValid && !loading && applicable !== null
      : isPluginStep
        ? stepState[currentStep.id]?.ready === true
        : false;

  const goNext = () => {
    if (!canLeaveCurrent || !nextStep) return;
    if (nextStep.id === CONFIGURE_STEP.id) enterConfigure();
    setCurrentId(nextStep.id);
  };
  const goBack = () => {
    if (stepIndex > 0) setCurrentId(steps[stepIndex - 1].id);
  };

  const handleCreate = async () => {
    if (yamlError) return;
    setCreating(true);
    setError(null);
    setCommitErrors(null);
    setSuccess(null);
    try {
      // Plugin steps act first, so the config can refer to what they made.
      // The first step that reports errors stops the create.
      for (const step of activePluginSteps) {
        if (!step.commit) continue;
        let result;
        try {
          result = await step.commit({
            workspaceName: typedName,
            config: workspaceConfig,
            value: stepState[step.id]?.value,
          });
        } catch (err) {
          console.error(`Flow step "${step.id}" failed:`, err);
          result = { errors: [err?.message || String(err)] };
        }
        if (result && result.value !== undefined) {
          onStepChange(step.id, { value: result.value });
        }
        const errors = result?.errors || [];
        if (errors.length > 0) {
          setCommitErrors({ step, errors });
          return;
        }
      }
      const created = await createWorkspace(typedName, workspaceConfig);
      invalidateWorkspaceCaches();
      setSuccess(`Workspace "${typedName}" created.`);
      // The server keys the new workspace by a generated id.
      const createdId =
        Object.keys(created || {}).find(
          (id) => created[id]?.name === typedName
        ) || typedName;
      setTimeout(() => {
        router.push(`/workspaces/${createdId}`);
      }, 1500);
    } catch (err) {
      console.error('Error creating workspace:', err);
      setError(err);
    } finally {
      setCreating(false);
    }
  };

  const cancel = () => router.push('/workspaces');
  const done = Boolean(success);
  const primaryClass = 'bg-sky-600 hover:bg-sky-700 text-white';

  let footer;
  if (currentStep.id === NAME_STEP.id) {
    footer = (
      <>
        <span className="text-[13px] text-gray-500">
          Nothing is created until you finish
        </span>
        <div className="flex gap-2.5">
          <Button variant="outline" size="sm" onClick={cancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={goNext}
            disabled={!canLeaveCurrent}
            title={
              isNameValid
                ? 'Configure the workspace'
                : 'Choose a name that no workspace has'
            }
            className={primaryClass}
          >
            {loading || applicable === null
              ? 'Loading...'
              : `Next: ${nextStep?.label}`}
          </Button>
        </div>
      </>
    );
  } else if (isPluginStep) {
    footer = (
      <>
        <Button
          variant="ghost"
          size="sm"
          onClick={goBack}
          className="px-0 text-gray-700 hover:bg-transparent"
        >
          Back
        </Button>
        <div className="flex gap-2.5">
          <Button variant="outline" size="sm" onClick={cancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={goNext}
            disabled={!canLeaveCurrent}
            className={primaryClass}
          >
            {`Next: ${nextStep?.label}`}
          </Button>
        </div>
      </>
    );
  } else {
    footer = (
      <>
        <Button
          variant="ghost"
          size="sm"
          onClick={goBack}
          disabled={creating || done}
          className="px-0 text-gray-700 hover:bg-transparent"
        >
          Back
        </Button>
        <div className="flex gap-2.5">
          <Button
            variant="outline"
            size="sm"
            onClick={cancel}
            disabled={creating || done}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={handleCreate}
            disabled={creating || done || Boolean(yamlError)}
            title={
              yamlError
                ? 'Fix the YAML first'
                : `Create workspace "${typedName}"`
            }
            className={primaryClass}
          >
            {creating ? 'Creating...' : 'Create workspace'}
          </Button>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base flex items-center">
          <Link href="/workspaces" className="text-sky-blue hover:underline">
            Workspaces
          </Link>
          <span className="mx-2 text-gray-500">›</span>
          <span className="text-sky-blue">New Workspace</span>
        </div>
      </div>

      <section
        aria-labelledby="new-workspace-title"
        className="max-w-[940px] rounded-lg border border-gray-200 bg-white"
      >
        <div className="border-b border-gray-200 px-5 py-3.5">
          <h2
            id="new-workspace-title"
            className="text-base font-semibold text-gray-900"
          >
            Create a workspace
          </h2>
        </div>

        <div className="space-y-5 p-5">
          <div className="space-y-3.5">
            {currentStep.id === NAME_STEP.id && (
              <p className="text-sm text-gray-500">
                Name the workspace, then choose the infrastructure it can use
                and who can access it
              </p>
            )}
            {applicable !== null && (
              <Stepper
                steps={steps.map((step) => step.label)}
                current={stepIndex + 1}
                label="Setup steps"
              />
            )}
          </div>

          {currentStep.id === NAME_STEP.id && (
            <div className="max-w-md">
              <Label htmlFor="workspace-name" className="text-sm font-normal">
                Workspace name
              </Label>
              <Input
                id="workspace-name"
                value={workspaceName}
                onChange={(e) => setWorkspaceName(e.target.value)}
                placeholder="Enter workspace name"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') goNext();
                }}
              />
              {nameError ? (
                <p className="text-sm text-red-600 mt-1">{nameError}</p>
              ) : isWorkspaceNameTaken ? (
                <p className="text-sm text-gray-500 mt-1">
                  {workspaceNameTakenMessage(
                    existingWorkspaces,
                    typedName,
                    taken
                  )}{' '}
                  <Link
                    href={`/workspaces/${taken.id}`}
                    className="text-blue-600 hover:underline"
                  >
                    View the workspace
                  </Link>
                </p>
              ) : (
                <p className="text-sm text-gray-500 mt-1">
                  Choose a unique name for your workspace
                </p>
              )}
            </div>
          )}

          {isPluginStep && (
            <PluginStep
              key={currentStep.id}
              step={currentStep}
              workspaceName={typedName}
              value={stepState[currentStep.id]?.value}
              onStepChange={onStepChange}
            />
          )}

          {currentStep.id === CONFIGURE_STEP.id && (
            <div className="space-y-4">
              <ErrorDisplay
                error={error}
                title="Couldn't create the workspace"
                onDismiss={() => setError(null)}
              />
              {commitErrors && (
                <Alert variant="destructive">
                  <AlertDescription>
                    <p className="font-medium">
                      {`The ${commitErrors.step.label} step could not finish. Go back to fix it, then create again.`}
                    </p>
                    <ul className="mt-1 list-disc pl-5">
                      {commitErrors.errors.map((message, i) => (
                        <li key={i}>{message}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
              {success && (
                <Alert className="border-green-200 bg-green-50">
                  <CheckIcon className="h-4 w-4 text-green-600" />
                  <AlertDescription className="text-green-800">
                    {success}
                  </AlertDescription>
                </Alert>
              )}

              <div>
                <h3 className="text-sm font-medium text-gray-900">
                  Workspace YAML
                </h3>
                <p className="text-sm text-gray-600 mt-1">
                  Configure infra-specific settings for this workspace. Leave
                  empty to use all accessible infrastructure. Refer to{' '}
                  <a
                    href="https://docs.skypilot.co/en/latest/admin/workspaces.html#configuration"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600"
                  >
                    SkyPilot Docs
                  </a>{' '}
                  for more details.
                </p>
              </div>

              <details className="rounded-lg border bg-gray-50">
                <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-gray-700">
                  Example configuration
                </summary>
                <pre className="px-3 pb-3 text-xs font-mono text-gray-600 whitespace-pre-wrap">
                  {`${typedName}:
  private: true
  # who can read it (default: allowed_users = members only; all = anyone)
  read_access: all
  allowed_users:
  - user1@mydomain.com
  - user2@mydomain.com
  gcp:
    project_id: xxx
    disabled: false
  kubernetes:
    allowed_contexts:
    - context-1`}
                </pre>
              </details>

              {yamlError && <ErrorDisplay error={yamlError} />}
              <YamlEditor
                value={yamlValue}
                onChange={(val) => applyYaml(val, typedName)}
                height="360px"
                disabled={creating || done}
              />
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 rounded-b-lg border-t border-gray-200 bg-gray-50 px-5 py-3.5">
          {footer}
        </div>
      </section>
    </>
  );
}
