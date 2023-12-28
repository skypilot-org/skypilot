"""LLM Profiler: offline profiling for LLM serving performance"""

class LLMProfiler:
  def __init__(self, yaml_config, prompts):
    self.yaml_config = yaml_config
    self.prompts = prompts
    print("TODO")

  def setup(self):
    print("TODO")

  def profile_max_throughput(self, prompt):
    print("TODO")

  def profile(self):
    self.setup()
    for prompt in self.prompts:
      self.profile_max_throughput(prompt)
    print("TODO")