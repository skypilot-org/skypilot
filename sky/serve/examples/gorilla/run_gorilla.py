# Code is borrowed from gorilla's colab
# https://colab.research.google.com/drive/1DEBPsccVLF_aUnmD0FwPeHFrtdC0QIUP?usp=sharing  # pylint: disable=line-too-long

import urllib.parse

import openai

openai.api_key = "EMPTY"  # Key is ignored and does not matter
# SkyServe endpoint
endpoint = input("Enter SkyServe endpoint: ")
# endpoint = '34.132.127.197:8000'
openai.api_base = f"http://{endpoint}/v1"


# Report issues
def raise_issue(e, model, prompt):
    issue_title = urllib.parse.quote("[bug] Hosted Gorilla: <Issue>")
    issue_body = urllib.parse.quote(
        f"Exception: {e}\nFailed model: {model}, for prompt: {prompt}")
    issue_url = f"https://github.com/ShishirPatil/gorilla/issues/new?assignees=&labels=hosted-gorilla&projects=&template=hosted-gorilla-.md&title={issue_title}&body={issue_body}"
    print(
        f"An exception has occurred: {e} \nPlease raise an issue here: {issue_url}"
    )


# Query Gorilla server
def get_gorilla_response(prompt, model="gorilla-mpt-7b-hf-v0"):
    try:
        completion = openai.ChatCompletion.create(model=model,
                                                  messages=[{
                                                      "role": "user",
                                                      "content": prompt
                                                  }])
        return completion.choices[0].message.content
    except Exception as e:
        raise_issue(e, model, prompt)


prompt = "I would like to translate 'I feel very good today.' from English to Chinese."
print(get_gorilla_response(prompt))

prompt = "I want to build a robot that can detecting objects in an image ‘cat.jpeg’. Input: [‘cat.jpeg’]"
print(get_gorilla_response(prompt))
