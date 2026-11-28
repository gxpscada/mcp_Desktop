import unittest
from calibration import calculate, ratio, temperature


class CalibrationTests(unittest.TestCase):
    def data(self, mode='adc', r0=1000, rref=4300, b=-20, k=1.05):
        inputs = dict(r0=str(r0), reference='50')
        for key, ref in [('zero', 0), ('fixed', 29.76), ('custom', 50)]:
            raw = 32768*r0/rref*ratio(ref/k)-b
            value = raw if mode == 'adc' else temperature(raw*rref/(32768*r0))
            inputs[mode+'_'+key] = str(value)
        return inputs

    def test_adc_three_points_pt100_and_pt1000(self):
        for r0, rref in [(100, 430), (1000, 4300)]:
            result = calculate(self.data(r0=r0, rref=rref), rref*10)
            self.assertEqual((result['b'], result['k_x100']), (-20, 105))
            self.assertLess(result['max_error'], 1e-7)

    def test_temperature_two_points_without_zero(self):
        data = self.data(mode='temp', b=12, k=.97)
        del data['temp_zero']
        result = calculate(data, 43000)
        self.assertEqual((result['b'], result['k_x100']), (12, 97))

    def test_mixed_points_and_adc_priority(self):
        data = self.data()
        temperatures = self.data(mode='temp')
        del data['adc_custom']
        del data['adc_fixed']
        data['temp_fixed'] = temperatures['temp_fixed'].replace('.', ',')
        data['temp_zero'] = '123'  # ADC at the same reference has priority.
        result = calculate(data, 43000)
        self.assertEqual((result['b'], result['k_x100']), (-20, 105))

    def test_invalid_points_do_not_produce_coefficients(self):
        cases = [dict(r0='1000', adc_zero='7600'),
                 dict(r0='1000', adc_zero='7600', temp_zero='0'),
                 dict(r0='1000', adc_zero='7600', adc_custom='7700', reference='0'),
                 dict(r0='1000', adc_zero='7600', adc_fixed='7500'),
                 dict(r0='1000', adc_zero='nan', adc_fixed='8000'),
                 dict(r0='1000', adc_zero='7600', adc_fixed='65535')]
        for inputs in cases:
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                calculate(inputs, 43000)
