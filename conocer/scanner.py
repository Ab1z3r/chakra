import os
import logging
import grpc
import copy

from transformers import AutoTokenizer
import transformers 
import torch
from contextlib import contextmanager

from .generator import DetoxioPromptGenerator
from .evaluator import DetoxioPromptResponseEvaluator
import proto.dtx.services.prompts.v1.prompts_pb2 as prompts_pb2
import proto.dtx.services.prompts.v1.prompts_pb2_grpc as prompts_pb2_grpc
import proto.dtx.messages.common.llm_pb2 as llm_pb2
import google.protobuf.empty_pb2 as empty_pb2
from google.protobuf.json_format import MessageToDict

class DetoxioModelDynamicScanner(object):
    """
    DetoxioModelDynamicScanner class for dynamic scanning using detoxio.ai.

    This class provides a context manager for creating and managing connections to detoxio.ai services.
    It includes methods for generating prompts and evaluating model responses for vulnerability.

    """
    def __init__(self, host='api.detoxio.ai', port=443, api_key=None):
        """
        Initialize DetoxioModelDynamicScanner.

        Args:
            host (str): Hostname of the detoxio.ai API (default is 'api.detoxio.ai').
            port (int): Port number for the detoxio.ai API (default is 443).
            api_key (str): API key for authentication (if not provided, check for DETOXIO_API_KEY environment variable).

        Raises:
            ValueError: If the API key is not provided.
        """
        # Verify we have an API key or fail fast
        dtx_api_host = host
        dtx_api_port = port
        detoxio_api_key = api_key or os.getenv('DETOXIO_API_KEY')
        if detoxio_api_key is None:
            raise ValueError('Please set DETOXIO_API_KEY environment variable')

        logging.debug(f"Creating gRPC channel to detoxio.ai backend at {dtx_api_host}:{dtx_api_port}")

        # Initialize connection to detoxio.ai backend
        token = grpc.access_token_call_credentials(detoxio_api_key)
        self._channel = grpc.secure_channel(f'{dtx_api_host}:{dtx_api_port}',
            grpc.composite_channel_credentials(grpc.ssl_channel_credentials(), token))

        self._client = prompts_pb2_grpc.PromptServiceStub(self._channel) 
        self._generator = DetoxioPromptGenerator(self._client)
        self._evaluator = DetoxioPromptResponseEvaluator(self._client)
    

    def is_alive(self):
        return self._client.Ping(empty_pb2.Empty())


    @contextmanager
    def new_session(self):
        yield DetoxioModelDynamicScannerSession(self._generator, self._evaluator)


class InMemoryScannerResults:

    def __init__(self):
        self._results = []
    
    def add_result(self, prompt, model_output, evaluation_response, model_name="default"):
        result = {
            "prompt": prompt.data.content,
            "results": {
                model_name: {
                    "model_output": model_output,
                    "evaluation_results": MessageToDict(evaluation_response),
                }
            }
        }
        self._results.append(result)
    
    def as_dict(self):
        return copy.copy(self._results)


class DetoxioModelDynamicScannerSession:

    def __init__(self, generator, evaluator):
        self._generator = generator
        self._evaluator = evaluator
        self._report = InMemoryScannerResults()

    def __enter__(self):
        # Nothing special to do when entering the context
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Clean up resources if needed when exiting the context
        pass

    def generate(self, count=10):
        """
        Generate a specified number of prompts.

        Args:
            count (int): Number of prompts to generate (default is 10).

        Returns:
            Generator of prompts.
        """
        return self._generator.generate(count=count)

    def evaluate(self, prompt: prompts_pb2.Prompt, model_output_text: str) -> prompts_pb2.PromptEvaluationResponse:
        """
        Evaluate a model interaction for vulnerability.

        Args:
            prompt: Prompt object to be evaluated.
            model_output_text: Text generated by the model in response to the prompt.

        Returns:
            Evaluation response as a prompts_pb2.PromptEvaluationResponse object.
        """
        eval_result = self._evaluator.evaluate(prompt, model_output_text)
        self._report.add_result(prompt, model_output_text, eval_result)
        return eval_result
    
    def get_report(self):
        return self._report
