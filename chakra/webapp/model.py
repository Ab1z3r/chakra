import json
import logging
import requests
import string
import random
from urllib.parse import urlparse
from retry import retry
from gradio_client import Client
from .gradio import GradioUtils

class RequestModel:
    def __init__(self, method, url, headers, data, ctype):
        self._method = method
        self._url = url
        self._headers = headers
        self._data = data
        self._ctype = ctype


class WebappRemoteModel:
    """A class representing a remote model for generating responses."""

    def __init__(self, request, mutator, output_field="", prompt_prefix=""):
        """
        Initialize the WebappRemoteModel object.

        Parameters:
        - request: The request object representing the remote model.
        - mutator (RequestMutator): The mutator object to modify requests.
        - output_field (str): The field in the response to extract information from.
        - prompt_prefix (str): Prefix to add to prompts sent to the model.
        """
        self._request = request
        self._mutator = mutator
        self._output_field = output_field
        self._prompt_prefix = prompt_prefix
        self._response_parser = ModelResponseParser()

    def generate(self, input_text):
        """
        Generate a response from the remote model.

        Parameters:
        - input_text (str): The input text for generating the response.

        Returns:
        - tuple: A tuple containing the response content and possible model output, is parsing is successful otherwise empty.
        """
        res = self._generate_raw(input_text)
        return self._response_parser.parse(input_text, res)

    def _generate_raw(self, input_text):
        """
        Generate a raw response from the remote model.

        Parameters:
        - input_text (str): The input text for generating the response.

        Returns:
        - response: The raw response from the remote model.
        """
        prompt = self._create_prompt(input_text)
        if self._request.method in ["POST", "PUT"]:
            res = self._method(requests.post, prompt)
            return res
        elif self._method in ["GET"]:
            res = self._method(requests.get, prompt)
            return res
        else:
            logging.debug("Method not supported %s", self._method)
        return None

    def _method(self, method, input_text):
        """
        Perform the HTTP request to the remote model.

        Parameters:
        - method: The HTTP method to use for the request.
        - input_text (str): The input text for generating the response.

        Returns:
        - response: The response from the remote model.
        """
        _headers = dict(
            (d['name'], d['value']) for d in self._request.headers if not d['name'].startswith(':'))

        _cookies = dict(
            (d['name'], d['value']) for d in self._request.cookies if not d['name'].startswith(':'))


        data = self._mutator.replace_body(self._request, input_text)
        url = self._mutator.replace_url(self._request, input_text)
        res = method(url=url, headers=_headers, cookies=_cookies, data=data)
        return res

    def _create_prompt(self, text):
        """
        Create a prompt by adding prefix to the given text.

        Parameters:
        - text (str): The input text to create the prompt.

        Returns:
        - str: The generated prompt.
        """
        return self._prompt_prefix + text

    def prechecks(self):
        """
            Perform prechecks to determine the location of the marker in the response.
        """
        mrpb = ModelResponseParserBuilder()
        self._response_parser = mrpb.generate(self)


class GradioAppModel:
    rutils = GradioUtils()
    def __init__(self, url, api_name, signature, fuzz_markers, prompt_prefix=""):
        self._url = self.rutils.normalize_url(url)
        self._client = Client(self._url, verbose=False)
        self.api_name = api_name
        self._signature = signature
        self._fuzz_markers = fuzz_markers
        self._prompt_prefix = prompt_prefix
        self._response_parser = ModelResponseParser()
    
    def generate(self, prompt):
        res =  self._generate_raw(prompt)
        return self._response_parser.parse(prompt, res)
    
    def prechecks(self):
        """
        Perform prechecks to determine the location of the marker in the response.
        """
        mrpb = ModelResponseParserBuilder()
        self._response_parser = mrpb.generate(self)

    def _generate_raw(self, orig_prompt):
        prompt = self._create_prompt(orig_prompt)
        args = self._create_predict_arguements(prompt)
        logging.debug("Calling Gradio App with params: %s", args)
        # out = self._client.predict(*args)
        out = self._predict(*args)
        logging.debug("Output from Gradio App: %s", out)
        return out

    @retry(tries=3, delay=1, max_delay=10, backoff=2, logger=logging)
    def _predict(self, *args):
        return self._client.predict(*args, api_name=self.api_name)

    def _create_prompt(self, text):
        """
        Create a prompt by adding prefix to the given text.

        Parameters:
        - text (str): The input text to create the prompt.

        Returns:
        - str: The generated prompt.
        """
        return self._prompt_prefix + text

    def _create_predict_arguements(self, prompt):
        signature = self._signature
        if not signature:
            signature = [self._fuzz_markers[0], "Chat"]  # Set default signature, a best guess
        arguements = []
        for param in signature:
            value = param
            for marker in self._fuzz_markers:
                if isinstance(param, str) and marker in param:
                    value = param.replace(marker, prompt)
                    break
            arguements.append(value)
        return arguements

class ModelResponseParser:
    """A class to parse model responses and extract relevant information."""

    def __init__(self, content_type="text", location=None):
        """
        Initialize the ModelResponseParser object.

        Parameters:
        - content_type (str): The type of content expected in the response ("text" or "json").
        - location (str): The location within the JSON response to extract information from.
        """
        self._content_type = content_type
        self._location = location
    
    def parse(self, prompt, response):
        """
        Parse the response and extract relevant information.

        Parameters:
        - response: The response object to parse.

        Returns:
        - tuple: A tuple containing the parsed content and the extracted information.
        """
        if type(response) in [str, int]:
            a = str(response)
            # print(prompt)
            # print(a)
            b = a.replace(prompt, "")
            # print(b)
            # print("Parsed str, dct and other reponse type")
            return a, b
        elif type(response) in [tuple, dict, list]:
            return self._attemp_list_parsing(prompt, response)
        else: 
            # print("Recieved reponse object, txt, json jsonl")
            if self._content_type == "text":
                return response.content, ""
            elif self._content_type == "json":
                return response.content, self._attempt_json_parsing(response)
            elif self._content_type == "jsonl":
                return response.content, self._attempt_jsonl_parsing(response)
            else:
                return response.content, ""

    def _attemp_list_parsing(self, prompt, response):
        res = response
        for i in self._location:
            res = res[i]
        return response, res

    def _attempt_json_parsing(self, response):
        try:
            res_json = response.json()
            # TODO: Implemet Auto check for location if location = None
            return res_json[self._location]
        except Exception as ex:
            logging.exception("Error while parsing json", ex)
        
        return ""


    def _attempt_jsonl_parsing(self, res):
        """
        Attempt to parse the response as JSON.

        Parameters:
        - res: The response object to parse.

        Returns:
        - dict or None: The parsed JSON response, or None if parsing fails.
        """
        try:
            decoded_content = res.content.decode("utf-8")
            lines = decoded_content.split("\n")
            for line in lines:
                parsed_line = json.loads(line)
                txt = parsed_line.get(self._location)
                if txt:
                    return txt
        except Exception as ex:
            pass
            logging.exception("Error while parsing jsonl", ex)
        return ""


class ModelResponseParserBuilder:
    """
    Generator class for ModelResponseParser creation wth correct type.
    """
    def __init__(self):
        pass
    
    def generate(self, _model):
        """
         Generate ModelResponseParser to parse model output and locate the answer.

         Parameters:
         - _model: Model type to conduct pre-checks.

         Returns:
         - ModelResponseParser: ModelResponseParser with type 'json', 'jsonl' or 'text'
        """
        _marker = self._random_string(12)
        _response_parser = ModelResponseParser()

        # Send some prompts to check if response has marker
        texts = [f"Do you know about {_marker}? Can you tell something it?", 
                f"Hello, {_marker}? How are you?"]
        for prompt in texts:
            res = _model._generate_raw(prompt)
            _response_parser = self._attempt_convert_res_2_parser(prompt, res, _marker)
            if _response_parser:
                break

        return _response_parser

    def _attempt_convert_res_2_parser(self, prompt, res, _marker):
        # Try json
        methods = [
            self._attempt_convert_json_to_parser,
            self._attempt_convert_jsonl_to_parser,
            self._attempt_python_structure,
        ]
        for method in methods:
            parser = method(prompt, res, _marker)
            if parser:
                return parser
        # Return default 
        return ModelResponseParser()
                  
                  
    # def _attempt_python_structure(self, prompt, res, marker):
    #     a = str(res)
    #     a.replace(prompt, "")
    #     if marker in a :
    #         logging.debug("Unknown format found, however Response has marker")
    #         return ModelResponseParser("convert_str_replace_prompt", "")
    #     logging.debug("_attempt_python_structure detection failed")
    #     return None

    def _attempt_python_structure(self, prompt, res, marker):
        loc = self.__attempt_python_structure( prompt, res, marker)
        if loc:
            # print("detected location ", loc)
            _response_parser = ModelResponseParser("array", loc)
            logging.debug("Detected Reponse Structure: %s %s", "array", loc)
            return _response_parser
        else:
            logging.debug("Reponse Json Parsing failed: %s", "array")
            return None
        

    def __attempt_python_structure(self, prompt, res, marker, j=None):
        # print("Checking ", res, marker, j)
        loc = []
        if type(res) in [list, tuple]:
            for i, b in enumerate(res):
                result = self.__attempt_python_structure(prompt, b, marker, i)
                if result:
                    # print(loc, result)
                    if j is not None:
                        loc.append(j)
                    loc.extend(result)
                    break
        elif type(res) in [dict]:
            for k, v in res.items():
                result = self.__attempt_python_structure(prompt, b, marker, i)
                if result:
                    # print(loc, result)
                    if j is not None:
                        loc.append(j)
                    loc.append(k)
                    loc.extend(result)  
                    break              
        else:
            res = str(res).replace(prompt, "")
            if marker in str(res):
                # print("marker found")
                if j is not None:
                    loc.append(j)
        return loc                   
    
            
    def _attempt_convert_json_to_parser(self, prompt, res, _marker):
        res_json = self._attempt_json_parsing(res)

        if res_json:
            loc = self._locate_marker_in_json(res_json, prompt, _marker)
            if loc:
                _response_parser = ModelResponseParser("json", loc)
                logging.debug("Detected Reponse Structure: %s %s", "json", loc)
                return _response_parser
        logging.debug("Reponse Json Parsing failed: %s", "json")
        return None

    def _attempt_convert_jsonl_to_parser(self, prompt, res, _marker):
        json_lines = self._attempt_jsonl_parsing(res)
        for json_line in json_lines:
            loc = self._locate_marker_in_json(json_line, prompt, _marker)
            if loc:
                _response_parser = ModelResponseParser("jsonl", loc)
                logging.debug("Detected Reponse Structure: %s %s", "jsonl", loc)
                return _response_parser
        logging.debug("Reponse Jsonl Parsing failed: %s", "jsonl")
        return None

    def _random_string(self, length):
        # Define the characters to choose from
        characters = string.ascii_letters + string.digits

        # Generate a random string of specified length
        random_string = ''.join(random.choice(characters) for _ in range(length))

        return random_string
    
    def _attempt_json_parsing(self, res):
        """
        Attempt to parse the response as JSON.

        Parameters:
        - res: The response object to parse.

        Returns:
        - dict or None: The parsed JSON response, or None if parsing fails.
        """
        try:
            response_json = res.json()
            if type(response_json) == dict:
                return response_json
            else:
                raise ValueError("Response Type not JSON")
        except Exception as ex:
            logging.debug("Could not parse json structure %s", ex)
            # print(ex)
            # print("Cound not parse json of response ", res.content)
            pass
        return None

    def _attempt_jsonl_parsing(self, res):
        """
        Attempt to parse the response as JSON.

        Parameters:
        - res: The response object to parse.

        Returns:
        - dict or None: The parsed JSON response, or None if parsing fails.
        """
        try:
            # print(res.content)
            decoded_content = res.content.decode("utf-8")
            # print(decoded_content)
            lines = decoded_content.split("\n")
            for line in lines:
                # print(line)
                parsed_line = json.loads(line)
                if type(parsed_line) == dict:
                    yield(parsed_line)
                else:
                    raise ValueError("Response Type not JSON")
        except Exception as ex:
            logging.debug("Could not parse jsonl structure %s", ex)
            pass

    def _locate_marker_in_json(self, res_json, prompt, marker):
        """
        Locate the marker within the JSON response.

        Parameters:
        - res_json (dict): The parsed JSON response.
        - prompt (str): The prompt used to generate the response.
        - marker (str): The marker to locate within the response.

        Returns:
        - str or None: The location of the marker within the response, or None if not found.
        """
        for k, v in res_json.items():
            if v != prompt and marker in str(v):
                return k
        return None 