#!/usr/bin/env python3
"""Relay infrastructure. Deploy with: cdk deploy (see README for the full steps)."""
import os

import aws_cdk as cdk

from relay_stack import RelayStack

app = cdk.App()
RelayStack(app, "RelayStack",
           env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                               region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")))
app.synth()
