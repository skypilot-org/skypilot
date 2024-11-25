#!/bin/bash

# Parse the webhook payload (read from stdin)
PAYLOAD=$(cat)

# Define the allowed user(s)
ALLOWED_USERS=("zpoint" "Michaelvll" "concretevitamin" "romilbhardwaj" "cg505" "yika-luo") # GitHub usernames

# Extract comment body and user info
COMMENT_BODY=$(echo "$PAYLOAD" | jq -r '.comment.body')
COMMENT_USER=$(echo "$PAYLOAD" | jq -r '.comment.user.login')

# Read the keyword from the first argument
KEYWORD="$1"

# Check if the comment contains the keyword and the user is authorized
if [[ "$COMMENT_BODY" == *"$KEYWORD"* ]] &&
   ( [[ " ${ALLOWED_USERS[@]} " =~ " $COMMENT_USER " ]]); then
    echo "Triggering build because $KEYWORD was mentioned by authorized user: $COMMENT_USER"
    exit 0  # Exit with success to continue the build
else
    echo "Build not triggered. Either $KEYWORD not found or user not authorized."
    exit 1  # Exit with failure to stop the build
fi
