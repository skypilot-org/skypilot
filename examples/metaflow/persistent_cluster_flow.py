from metaflow import FlowSpec, Parameter, step, pypi, skypilot

# The cluster is reused across runs.
CLUSTER_NAME = 'my-dev-cluster'


class TrainingFlow(FlowSpec):
    max_depth = Parameter('max_depth', default=5, type=int,
                          help='Max depth of the random forest.')

    @step
    def start(self):
        print(f"Training with max_depth={self.max_depth} on cluster: {CLUSTER_NAME}")
        self.next(self.train)

    @skypilot(cpus='4+', cluster_name=CLUSTER_NAME)
    @pypi(python='3.9.13', packages={'scikit-learn': '1.4.0', 'numpy': '1.26.0'})
    @step
    def train(self):
        from sklearn.datasets import make_classification
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split

        X, y = make_classification(n_samples=10_000, random_state=42)
        X_train, X_test, y_train, y_test = train_test_split(X, y, random_state=42)

        clf = RandomForestClassifier(max_depth=self.max_depth, random_state=42)
        clf.fit(X_train, y_train)
        self.accuracy = clf.score(X_test, y_test)
        print(f"Accuracy: {self.accuracy:.2%}")
        self.next(self.end)

    @step
    def end(self):
        print(f"Done! Accuracy: {self.accuracy:.2%}")


if __name__ == '__main__':
    TrainingFlow()
