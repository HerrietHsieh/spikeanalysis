import json
from utils import NumpyEncoder
from typing import Optional, Union

import neo
import numpy as np

from tqdm import tqdm


class StimulusData:
    """Class for preprocessing stimulus data for spike train analysis"""

    def __init__(self, file_path: str):
        """Enter the file_path as a string. For Windows prepend with r to prevent spurious escaping.
        A Path object can also be given, but make sure it was generated with a raw string"""
        from pathlib import Path
        import glob
        import os

        file_path = Path(file_path)
        assert Path.is_dir(
            file_path
        ), "Enter root directory with *rhd file. If having problems for \
        windows append r in front of the str."
        self._file_path = file_path
        os.chdir(file_path)
        try:
            filename = glob.glob("*rhd")[0]
        except IndexError:
            raise Exception("There is no rhd file present in this folder")
        self._filename = filename
        self.analog_data = None
        self.digital_data = None

    def get_all_files(self):
        import os

        os.chdir(self._file_path)
        import glob

        files = glob.glob("*json")
        assert len(files) > 0, "There are no previous files"

        files = "".join(files)

        if "digital_events" in files:
            with open("digital_events.json", "r") as read_file:
                self.digital_events = json.load(read_file)

        if "dig_analog" in files:
            with open("dig_analog_events.json") as read_file:
                self.dig_analog_events = json.load(read_file)
        try:
            raw_analog = glob.glob("raw_analog*")[0]
            self.analog_data = np.load(raw_analog)
        except IndexError:
            pass

    def run_all(
        self,
        stim_index: Optional[int] = None,
        stim_length_seconds: Optional[float] = None,
        stim_name: Optional[list] = None,
    ):
        self.create_neo_reader()
        try:
            self.get_analog_data()
            HAVE_ANALOG = True
        except AssertionError:
            HAVE_ANALOG = False

        self.get_raw_digital_data()
        try:
            len(np.isnan(self._raw_digital_data))
            HAVE_DIGITAL = True
        except TypeError:
            HAVE_DIGITAL = False

        if HAVE_ANALOG:
            self.digitize_analog_data(
                analog_index=stim_index,
                stim_length_seconds=stim_length_seconds,
                stim_name=stim_name,
            )
        if HAVE_DIGITAL:
            self.get_final_digital_data()
            self.generate_digital_events()

    def create_neo_reader(self):
        reader = neo.rawio.IntanRawIO(filename=self._filename)
        reader.parse_header()

        for value in reader.header["signal_channels"]:
            sample_freq = value[2]
            break
        self.sample_frequency = sample_freq
        self.reader = reader

    def get_analog_data(self):
        stream_list = list()
        for value in self.reader.header["signal_streams"]:
            stream_list.append(str(value[0]))
        adc_stream = [idx for idx, name in enumerate(stream_list) if "ADC" in name.upper()]
        assert len(adc_stream) > 0, "There is no analog data"
        adc_stream = adc_stream[0]
        adc_data = self.reader.get_analogsignal_chunk(
            stream_index=adc_stream,
        )

        final_adc = np.squeeze(
            self.reader.rescale_signal_raw_to_float(adc_data, stream_index=adc_stream, dtype="float64")
        )
        self.analog_data = final_adc

    def digitize_analog_data(
        self,
        analog_index: Optional[int] = None,
        stim_length_seconds: Optional[float] = None,
        stim_name: Optional[list[str]] = None,
    ):
        assert self.analog_data is not None, "There is no analog data"

        import statistics

        if stim_length_seconds is None:
            stim_length_seconds = 8 * self.sample_frequency
        else:
            stim_length_seconds *= self.sample_frequency
        if analog_index:
            current_analog_data = self.analog_data[analog_index, :]
        else:
            current_analog_data = self.analog_data

        if len(np.shape(current_analog_data)) == 1:
            current_analog_data = np.expand_dims(current_analog_data, axis=1)

        self.dig_analog_events = {}
        for row in tqdm(range(np.shape(current_analog_data)[1])):
            self.dig_analog_events[row] = {}
            sub_data = current_analog_data[:, row]
            filtered_analog_data = np.where(sub_data > 0.09, 1, 0)

            dig_ana_events, dig_ana_lengths = self._calculate_events(filtered_analog_data)
            events = dig_ana_events[dig_ana_lengths > stim_length_seconds]
            lengths = dig_ana_lengths[dig_ana_lengths > stim_length_seconds]
            trial_groups = np.zeros((len(events),))

            for idx in range(len(events)):
                start = events[idx]
                end = start + lengths[idx]
                trial_groups[idx] = int(self._valueround(statistics.mode(sub_data[start:end]) / 0.25))

            self.dig_analog_events[row]["events"] = events
            self.dig_analog_events[row]["lengths"] = lengths
            self.dig_analog_events[row]["trial_groups"] = trial_groups
            if stim_name is not None:
                self.dig_analog_events[row]["stim"] = stim_name[row]

    def _valueround(self, x: float, precision: int = 2, base: float = 0.25):
        return round(base * round(float(x) / base), precision)

    def get_raw_digital_data(self):
        # stream_list = list()
        # for value in self.reader.header["signal_streams"]:
        #    stream_list.append(str(value[0]))
        # digital_stream = [idx for idx, name in enumerate(stream_list) if "DIGITAL-IN" in name.upper()]
        # digital_stream = digital_stream[0]
        # assert len(digital_stream) >0, "There is no digital-in data"
        # digital_stream = digital_stream[0]
        try:
            digital_data = self._intan_neo_read_no_dig(self.reader)
        except:
            digital_data = np.nan

        self._raw_digital_data = digital_data

    def get_final_digital_data(self):
        try:
            len(np.isnan(self._raw_digital_data))

        except TypeError:
            raise Exception("There is no digital data present")

        fid = open(self._filename, "rb")
        intan_header = self._read_header(fid)
        fid.close()
        dig_in_channels = intan_header["board_dig_in_channels"]
        self.intan = intan_header

        values = np.zeros((len(dig_in_channels), len(self._raw_digital_data)))
        for value in range(len(dig_in_channels)):
            values[value, :] = np.not_equal(
                np.bitwise_and(
                    self._raw_digital_data,
                    (1 << dig_in_channels[value]["native_order"]),
                ),
                0,
            )
        self.digital_data = values
        self.dig_in_channels = dig_in_channels

    def generate_digital_events(self):
        assert self.digital_data is not None, "There is no final digital data, run get_final_digital_data first"

        self.digital_events = {}
        self.digital_channels = []
        for idx, row in enumerate(tqdm(self.digital_data)):
            self.digital_events[self.dig_in_channels[idx]["native_channel_name"]] = {}
            events, lengths = self._calculate_events(self.digital_data[idx])
            self.digital_events[self.dig_in_channels[idx]["native_channel_name"]]["events"] = events
            self.digital_events[self.dig_in_channels[idx]["native_channel_name"]]["lengths"] = lengths
            self.digital_events[self.dig_in_channels[idx]["native_channel_name"]]["trial_groups"] = np.ones(
                (len(events))
            )

            self.digital_channels.append(self.dig_in_channels[idx]["native_channel_name"])

    def get_stimulus_channels(self) -> dict:
        try:
            _ = self.digital_events
        except AttributeError:
            raise Exception("There are no digital events")

        stim_dict = {}
        for channel in self.digital_events.keys():
            stim_dict[channel] = ""

        return stim_dict

    def set_trial_groups(self, trial_dictionary: dict):
        try:
            for channel in self.digital_events.keys():
                self.digital_events[channel]["trial_groups"] = trial_dictionary[channel]
        except KeyError:
            raise Exception(
                f"Incorrect channel name. use get_stimulus_channels or create dict with \
                            keys of {self.digital_channels}"
            )

    def set_stimulus_name(self, stim_names: dict):
        try:
            for channel in self.digital_events.keys():
                assert isinstance(stim_names[channel], str), "stim names should be strings"
                self.digital_events[channel]["stim"] = stim_names[channel]
        except KeyError:
            raise Exception(
                f"Incorrect channel name. use get_stimulus_channels or create dict with \
                            keys of {self.digital_channels}"
            )

    def save_events(self):
        try:
            _ = self.digital_events

            with open("digital_events.json", "w") as write_file:
                json.dump(self.digital_events, write_file, cls=NumpyEncoder)
        except AttributeError:
            print("No digital events to save")

        try:
            _ = self.dig_analog_events
            with open("dig_analog_events.json", "w") as write_file:
                json.dump(self.dig_analog_events, write_file, cls=NumpyEncoder)
        except AttributeError:
            print("No analog events to save")

        try:
            np.save("raw_analog_data.npy", self.analog_data)
        except AttributeError:
            print("No raw analog data to save")

    def _intan_neo_read_no_dig(self, reader: neo.rawio.IntanRawIO) -> np.array:
        digital_memmap = reader._raw_data["DIGITAL-IN"]  # directly grab memory map from neo
        dig_size = digital_memmap.size
        dig_shape = digital_memmap.shape
        # below we have all the shaping information necessary
        i_start = 0
        i_stop = dig_size
        block_size = dig_shape[1]
        block_start = i_start // block_size
        block_stop = i_stop // block_size + 1

        sl0 = i_start % block_size
        sl1 = sl0 + (i_stop - i_start)

        raw_digital_data = np.squeeze(digital_memmap[block_start:block_stop].flatten()[sl0:sl1])

        return raw_digital_data

    def _calculate_events(self, array: np.array) -> tuple[np.array, np.array]:
        sq_array = np.array(np.squeeze(array), dtype=np.int16)
        onset = np.where(np.diff(sq_array) == 1)[0]
        offset = np.where(np.diff(sq_array) == -1)[0]
        if sq_array[0] == 1:
            onset = np.pad(onset, (1, 0), "constant", constant_values=0)
        if sq_array[-1] == 1:
            offset = np.pad(offset, (0, 1), "constant", constant_values=sq_array[-1])
        lengths = offset - onset

        return onset, lengths

    def _read_header(self, fid):
        # Michael Gibson 23 APRIL 2015
        # Adrian Foy Sep 2018
        import struct

        (magic_number,) = struct.unpack("<I", fid.read(4))
        if magic_number != int("c6912702", 16):
            raise Exception("Unrecognized file type.")

        header = {}
        version = {}
        (version["major"], version["minor"]) = struct.unpack("<hh", fid.read(4))
        header["version"] = version

        freq = {}

        # Read information of sampling rate and amplifier frequency settings.
        (header["sample_rate"],) = struct.unpack("<f", fid.read(4))
        (
            freq["dsp_enabled"],
            freq["actual_dsp_cutoff_frequency"],
            freq["actual_lower_bandwidth"],
            freq["actual_upper_bandwidth"],
            freq["desired_dsp_cutoff_frequency"],
            freq["desired_lower_bandwidth"],
            freq["desired_upper_bandwidth"],
        ) = struct.unpack("<hffffff", fid.read(26))

        # This tells us if a software 50/60 Hz notch filter was enabled during
        # the data acquisition.
        (notch_filter_mode,) = struct.unpack("<h", fid.read(2))
        header["notch_filter_frequency"] = 0
        if notch_filter_mode == 1:
            header["notch_filter_frequency"] = 50
        elif notch_filter_mode == 2:
            header["notch_filter_frequency"] = 60
        freq["notch_filter_frequency"] = header["notch_filter_frequency"]

        (
            freq["desired_impedance_test_frequency"],
            freq["actual_impedance_test_frequency"],
        ) = struct.unpack("<ff", fid.read(8))

        note1 = self._read_qstring(fid)
        note2 = self._read_qstring(fid)
        note3 = self._read_qstring(fid)
        header["notes"] = {"note1": note1, "note2": note2, "note3": note3}

        # If data file is from GUI v1.1 or later, see if temperature sensor data was saved.
        header["num_temp_sensor_channels"] = 0
        if (version["major"] == 1 and version["minor"] >= 1) or (version["major"] > 1):
            (header["num_temp_sensor_channels"],) = struct.unpack("<h", fid.read(2))

        # If data file is from GUI v1.3 or later, load eval board mode.
        header["eval_board_mode"] = 0
        if ((version["major"] == 1) and (version["minor"] >= 3)) or (version["major"] > 1):
            (header["eval_board_mode"],) = struct.unpack("<h", fid.read(2))

        header["num_samples_per_data_block"] = 60
        # If data file is from v2.0 or later (Intan Recording Controller), load name of digital reference channel
        if version["major"] > 1:
            header["reference_channel"] = self._read_qstring(fid)
            header["num_samples_per_data_block"] = 128

        # Place frequency-related information in data structure. (Note: much of this structure is set above)
        freq["amplifier_sample_rate"] = header["sample_rate"]
        freq["aux_input_sample_rate"] = header["sample_rate"] / 4
        freq["supply_voltage_sample_rate"] = header["sample_rate"] / header["num_samples_per_data_block"]
        freq["board_adc_sample_rate"] = header["sample_rate"]
        freq["board_dig_in_sample_rate"] = header["sample_rate"]

        header["frequency_parameters"] = freq

        # Create structure arrays for each type of data channel.
        header["spike_triggers"] = []
        header["amplifier_channels"] = []
        header["aux_input_channels"] = []
        header["supply_voltage_channels"] = []
        header["board_adc_channels"] = []
        header["board_dig_in_channels"] = []
        header["board_dig_out_channels"] = []

        # Read signal summary from data file header.

        (number_of_signal_groups,) = struct.unpack("<h", fid.read(2))
        # print("n signal groups {}".format(number_of_signal_groups))

        for signal_group in tqdm(range(1, number_of_signal_groups + 1)):
            signal_group_name = self._read_qstring(fid)
            signal_group_prefix = self._read_qstring(fid)
            (
                signal_group_enabled,
                signal_group_num_channels,
                signal_group_num_amp_channels,
            ) = struct.unpack("<hhh", fid.read(6))

            if (signal_group_num_channels > 0) and (signal_group_enabled > 0):
                for signal_channel in range(0, signal_group_num_channels):
                    new_channel = {
                        "port_name": signal_group_name,
                        "port_prefix": signal_group_prefix,
                        "port_number": signal_group,
                    }
                    new_channel["native_channel_name"] = self._read_qstring(fid)
                    new_channel["custom_channel_name"] = self._read_qstring(fid)
                    (
                        new_channel["native_order"],
                        new_channel["custom_order"],
                        signal_type,
                        channel_enabled,
                        new_channel["chip_channel"],
                        new_channel["board_stream"],
                    ) = struct.unpack("<hhhhhh", fid.read(12))
                    new_trigger_channel = {}
                    (
                        new_trigger_channel["voltage_trigger_mode"],
                        new_trigger_channel["voltage_threshold"],
                        new_trigger_channel["digital_trigger_channel"],
                        new_trigger_channel["digital_edge_polarity"],
                    ) = struct.unpack("<hhhh", fid.read(8))
                    (
                        new_channel["electrode_impedance_magnitude"],
                        new_channel["electrode_impedance_phase"],
                    ) = struct.unpack("<ff", fid.read(8))

                    if channel_enabled:
                        if signal_type == 0:
                            header["amplifier_channels"].append(new_channel)
                            header["spike_triggers"].append(new_trigger_channel)
                        elif signal_type == 1:
                            header["aux_input_channels"].append(new_channel)
                        elif signal_type == 2:
                            header["supply_voltage_channels"].append(new_channel)
                        elif signal_type == 3:
                            header["board_adc_channels"].append(new_channel)
                        elif signal_type == 4:
                            header["board_dig_in_channels"].append(new_channel)
                        elif signal_type == 5:
                            header["board_dig_out_channels"].append(new_channel)
                        else:
                            raise Exception("Unknown channel type.")

        # Summarize contents of data file.
        header["num_amplifier_channels"] = len(header["amplifier_channels"])
        header["num_aux_input_channels"] = len(header["aux_input_channels"])
        header["num_supply_voltage_channels"] = len(header["supply_voltage_channels"])
        header["num_board_adc_channels"] = len(header["board_adc_channels"])
        header["num_board_dig_in_channels"] = len(header["board_dig_in_channels"])
        header["num_board_dig_out_channels"] = len(header["board_dig_out_channels"])

        return header

    def _read_qstring(self, fid):
        # Michael Gibson 23APRIL2015
        # ZM some changes
        import struct, os

        (length,) = struct.unpack("<I", fid.read(4))
        if length == int("ffffffff", 16):
            return ""

        if length > (os.fstat(fid.fileno()).st_size - fid.tell() + 1):
            print(length)
            raise Exception("Length too long.")

        # convert length from bytes to 16-bit Unicode words
        length = int(length / 2)

        data = []
        for _ in range(0, length):
            (c,) = struct.unpack("<H", fid.read(2))
            data.append(c)

        a = "".join([chr(c) for c in data])

        return a

